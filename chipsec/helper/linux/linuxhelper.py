# CHIPSEC: Platform Security Assessment Framework
# Copyright (c) 2010-2021, Intel Corporation
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; Version 2.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
# Contact information:
# chipsec@intel.com
#

"""
Linux helper
"""

import array
import errno
import fcntl
import os
import platform
import struct
import subprocess
import sys

from chipsec import defines
from chipsec.helper.oshelper import get_tools_path
from chipsec.exceptions import OsHelperError, UnimplementedAPIError
from chipsec.helper.basehelper import Helper
from chipsec.logger import logger
import chipsec.file

MSGBUS_MDR_IN_MASK = 0x1
MSGBUS_MDR_OUT_MASK = 0x2

IOCTL_BASE                     = 0x0
IOCTL_RDIO                     = 0x1
IOCTL_WRIO                     = 0x2
IOCTL_RDPCI                    = 0x3
IOCTL_WRPCI                    = 0x4
IOCTL_RDMSR                    = 0x5
IOCTL_WRMSR                    = 0x6
IOCTL_CPUID                    = 0x7
IOCTL_GET_CPU_DESCRIPTOR_TABLE = 0x8
IOCTL_HYPERCALL                = 0x9
IOCTL_SWSMI                    = 0xA
IOCTL_LOAD_UCODE_PATCH         = 0xB
IOCTL_ALLOC_PHYSMEM            = 0xC
IOCTL_GET_EFIVAR               = 0xD
IOCTL_SET_EFIVAR               = 0xE
IOCTL_RDCR                     = 0x10
IOCTL_WRCR                     = 0x11
IOCTL_RDMMIO                   = 0x12
IOCTL_WRMMIO                   = 0x13
IOCTL_VA2PA                    = 0x14
IOCTL_MSGBUS_SEND_MESSAGE      = 0x15
IOCTL_FREE_PHYSMEM             = 0x16

_tools = {}


class LinuxHelper(Helper):

    DEVICE_NAME = "/dev/chipsec"
    DEV_MEM = "/dev/mem"
    DEV_PORT = "/dev/port"
    MODULE_NAME = "chipsec"
    SUPPORT_KERNEL26_GET_PAGE_IS_RAM = False
    SUPPORT_KERNEL26_GET_PHYS_MEM_ACCESS_PROT = False
    DKMS_DIR = "/var/lib/dkms/"

    def __init__(self):
        super(LinuxHelper, self).__init__()
        self.os_system = platform.system()
        self.os_release = platform.release()
        self.os_version = platform.version()
        self.os_machine = platform.machine()
        self.os_uname = platform.uname()
        self.name = "LinuxHelper"
        self.dev_fh = None
        self.dev_mem = None
        self.dev_port = None
        self.dev_msr = None

        # A list of all the mappings allocated via map_io_space. When using
        # read/write MMIO, if the region is already mapped in the process's
        # memory, simply read/write from there.
        self.mappings = []

###############################################################################################
# Driver/service management functions
###############################################################################################

    def get_dkms_module_location(self):
        version = defines.get_version()
        from os import listdir
        from os.path import isdir, join
        p = os.path.join(self.DKMS_DIR, self.MODULE_NAME, version, self.os_release)
        os_machine_dir_name = [f for f in listdir(p) if isdir(join(p, f))][0]
        return os.path.join(self.DKMS_DIR, self.MODULE_NAME, version, self.os_release, os_machine_dir_name, "module", "chipsec.ko")

    # This function load CHIPSEC driver
    def load_chipsec_module(self):
        page_is_ram = ""
        phys_mem_access_prot = ""
        a1 = ""
        a2 = ""
        if self.SUPPORT_KERNEL26_GET_PAGE_IS_RAM:
            page_is_ram = self.get_page_is_ram()
            if not page_is_ram:
                logger().log_debug("Cannot find symbol 'page_is_ram'")
            else:
                a1 = "a1=0x{}".format(page_is_ram)
        if self.SUPPORT_KERNEL26_GET_PHYS_MEM_ACCESS_PROT:
            phys_mem_access_prot = self.get_phys_mem_access_prot()
            if not phys_mem_access_prot:
                logger().log_debug("Cannot find symbol 'phys_mem_access_prot'")
            else:
                a2 = "a2=0x{}".format(phys_mem_access_prot)

        driver_path = os.path.join(chipsec.file.get_main_dir(), "chipsec", "helper", "linux", "chipsec.ko")
        if not os.path.exists(driver_path):
            driver_path += ".xz"
            if not os.path.exists(driver_path):
                # check DKMS modules location
                try:
                    driver_path = self.get_dkms_module_location()
                except Exception:
                    pass
                if not os.path.exists(driver_path):
                    driver_path += ".xz"
                    if not os.path.exists(driver_path):
                        raise Exception("Cannot find chipsec.ko module")
        try:
            subprocess.check_output(["insmod", driver_path, a1, a2])
        except Exception as err:
            raise Exception("Could not start Linux Helper, are you running as Admin/root?\n\t{}".format(err))
        uid = gid = 0
        os.chown(self.DEVICE_NAME, uid, gid)
        os.chmod(self.DEVICE_NAME, 600)
        if os.path.exists(self.DEVICE_NAME):
            logger().log_debug("Module {} loaded successfully".format(self.DEVICE_NAME))
        else:
            logger().error("Fail to load module: {}".format(driver_path))
        self.driverpath = driver_path

    def create(self, start_driver):
        logger().log_debug("[helper] Linux Helper created")
        return True

    def start(self, start_driver, driver_exists=False):
        if start_driver:
            if os.path.exists(self.DEVICE_NAME):
                subprocess.call(["rmmod", self.MODULE_NAME])
            self.load_chipsec_module()
        self.init(start_driver)
        logger().log_debug("[helper] Linux Helper started/loaded")
        return True

    def stop(self, start_driver):
        self.close()
        if self.driver_loaded:
            subprocess.call(["rmmod", self.MODULE_NAME])
        logger().log_debug("[helper] Linux Helper stopped/unloaded")
        return True

    def delete(self, start_driver):
        logger().log_debug("[helper] Linux Helper deleted")
        return True

    def init(self, start_driver):
        x64 = True if sys.maxsize > 2**32 else False
        self._pack = 'Q' if x64 else 'I'

        if start_driver:
            logger().log("****** Chipsec Linux Kernel module is licensed under GPL 2.0")

            try:
                self.dev_fh = open(self.DEVICE_NAME, "rb+")
                self.driver_loaded = True
            except IOError as e:
                raise OsHelperError("Unable to open chipsec device. Did you run as root/sudo and load the driver?\n {}".format(str(e)), e.errno)
            except BaseException as be:
                raise OsHelperError("Unable to open chipsec device. Did you run as root/sudo and load the driver?\n {}".format(str(be)), errno.ENXIO)

            self._ioctl_base = self.compute_ioctlbase()

    def devmem_available(self):
        """Check if /dev/mem is usable.

           In case the driver is not loaded, we might be able to perform the
           requested operation via /dev/mem. Returns True if /dev/mem is
           accessible.
        """
        if self.dev_mem:
            return True

        try:
            self.dev_mem = os.open(self.DEV_MEM, os.O_RDWR)
            return True
        except IOError as err:
            raise OsHelperError("Unable to open /dev/mem.\n"
                                "This command requires access to /dev/mem.\n"
                                "Are you running this command as root?\n"
                                "{}".format(str(err)), err.errno)

    def close(self):
        if self.dev_fh:
            self.dev_fh.close()
        self.dev_fh = None
        if self.dev_mem:
            os.close(self.dev_mem)
        self.dev_mem = None

    # code taken from /include/uapi/asm-generic/ioctl.h
    # by default itype is 'C' see drivers/linux/include/chipsec.h
    # currently all chipsec ioctl functions are _IOWR
    # currently all size are pointer
    def compute_ioctlbase(self, itype='C'):
        # define _IOWR(type,nr,size)	 _IOC(_IOC_READ|_IOC_WRITE,(type),(nr),(_IOC_TYPECHECK(size)))
        # define _IOC(dir,type,nr,size) \
        #    (((dir)  << _IOC_DIRSHIFT) | \
        #    ((type) << _IOC_TYPESHIFT) | \
        #    ((nr)   << _IOC_NRSHIFT) | \
        #    ((size) << _IOC_SIZESHIFT))
        # IOC_READ | _IOC_WRITE is 3
        # default _IOC_DIRSHIFT is 30
        # default _IOC_TYPESHIFT is 8
        # nr will be 0
        # _IOC_SIZESHIFT is 16
        return (3 << 30) | (ord(itype) << 8) | (struct.calcsize(self._pack) << 16)

    def ioctl(self, nr, args, *mutate_flag):
        return fcntl.ioctl(self.dev_fh, self._ioctl_base + nr, args)

###############################################################################################
# Actual API functions to access HW resources
###############################################################################################
    def map_io_space(self, base, size, cache_type):
        raise UnimplementedAPIError("map_io_space")

    def __mem_block(self, sz, newval=None):
        if newval is None:
            return self.dev_fh.read(sz)
        else:
            self.dev_fh.write(newval)
            self.dev_fh.flush()
        return 1

    def write_phys_mem(self, phys_address_hi, phys_address_lo, length, newval):
        if newval is None:
            return None
        addr = (phys_address_hi << 32) | phys_address_lo
        self.dev_fh.seek(addr)
        return self.__mem_block(length, newval)

    def read_phys_mem(self, phys_address_hi, phys_address_lo, length):
        addr = (phys_address_hi << 32) | phys_address_lo
        self.dev_fh.seek(addr)
        return self.__mem_block(length)

    def va2pa(self, va):
        error_code = 0

        in_buf = struct.pack(self._pack, va)
        out_buf = self.ioctl(IOCTL_VA2PA, in_buf)
        pa = struct.unpack(self._pack, out_buf)[0]

        # Check if PA > max physical address
        max_pa = self.cpuid(0x80000008, 0x0)[0] & 0xFF
        if pa > 1 << max_pa:
            logger().log_debug("[helper] Error in va2pa: PA higher that max physical address: VA (0x{:016X}) -> PA (0x{:016X})".format(va, pa))
            error_code = 1
        return (pa, error_code)

    def read_pci_reg(self, bus, device, function, offset, size=4):
        _PCI_DOM = 0  # Change PCI domain, if there is more than one.
        d = struct.pack("5" + self._pack, ((_PCI_DOM << 16) | bus), ((device << 16) | function), offset, size, 0)
        try:
            ret = self.ioctl(IOCTL_RDPCI, d)
        except IOError:
            logger().log_debug("IOError\n")
            return None
        x = struct.unpack("5" + self._pack, ret)
        return x[4]

    def write_pci_reg(self, bus, device, function, offset, value, size=4):
        _PCI_DOM = 0  # Change PCI domain, if there is more than one.
        d = struct.pack("5" + self._pack, ((_PCI_DOM << 16) | bus), ((device << 16) | function), offset, size, value)
        try:
            ret = self.ioctl(IOCTL_WRPCI, d)
        except IOError:
            logger().log_debug("IOError\n")
            return None
        x = struct.unpack("5" + self._pack, ret)
        return x[4]

    def load_ucode_update(self, cpu_thread_id, ucode_update_buf):
        in_buf = struct.pack('=BH', cpu_thread_id, len(ucode_update_buf)) + ucode_update_buf
        in_buf_final = array.array("c", in_buf)
        try:
            out_buf = self.ioctl(IOCTL_LOAD_UCODE_PATCH, in_buf_final)
        except IOError:
            logger().log_debug("IOError IOCTL Load Patch\n")
            return None

        return True

    def read_io_port(self, io_port, size):
        in_buf = struct.pack("3" + self._pack, io_port, size, 0)
        out_buf = self.ioctl(IOCTL_RDIO, in_buf)
        try:
            if 1 == size:
                value = struct.unpack("3" + self._pack, out_buf)[2] & 0xff
            elif 2 == size:
                value = struct.unpack("3" + self._pack, out_buf)[2] & 0xffff
            else:
                value = struct.unpack("3" + self._pack, out_buf)[2] & 0xffffffff
        except Exception:
            logger().log_debug("DeviceIoControl did not return value of proper size {:x} (value = '{}')".format(size, out_buf))

        return value

    def write_io_port(self, io_port, value, size):
        in_buf = struct.pack("3" + self._pack, io_port, size, value)
        return self.ioctl(IOCTL_WRIO, in_buf)

    def read_cr(self, cpu_thread_id, cr_number):
        self.set_affinity(cpu_thread_id)
        cr = 0
        in_buf = struct.pack("3" + self._pack, cpu_thread_id, cr_number, cr)
        unbuf = struct.unpack("3" + self._pack, self.ioctl(IOCTL_RDCR, in_buf))
        return (unbuf[2])

    def write_cr(self, cpu_thread_id, cr_number, value):
        self.set_affinity(cpu_thread_id)
        in_buf = struct.pack("3" + self._pack, cpu_thread_id, cr_number, value)
        self.ioctl(IOCTL_WRCR, in_buf)
        return

    def read_msr(self, thread_id, msr_addr):
        self.set_affinity(thread_id)
        edx = eax = 0
        in_buf = struct.pack("4" + self._pack, thread_id, msr_addr, edx, eax)
        unbuf = struct.unpack("4" + self._pack, self.ioctl(IOCTL_RDMSR, in_buf))
        return (unbuf[3], unbuf[2])

    def write_msr(self, thread_id, msr_addr, eax, edx):
        self.set_affinity(thread_id)
        in_buf = struct.pack("4" + self._pack, thread_id, msr_addr, edx, eax)
        self.ioctl(IOCTL_WRMSR, in_buf)
        return

    def get_descriptor_table(self, cpu_thread_id, desc_table_code):
        self.set_affinity(cpu_thread_id)
        in_buf = struct.pack("5" + self._pack, cpu_thread_id, desc_table_code, 0, 0, 0)
        out_buf = self.ioctl(IOCTL_GET_CPU_DESCRIPTOR_TABLE, in_buf)
        (limit, base_hi, base_lo, pa_hi, pa_lo) = struct.unpack("5" + self._pack, out_buf)
        pa = (pa_hi << 32) + pa_lo
        base = (base_hi << 32) + base_lo
        return (limit, base, pa)

    def cpuid(self, eax, ecx):
        # add ecx
        in_buf = struct.pack("4" + self._pack, eax, 0, ecx, 0)
        out_buf = self.ioctl(IOCTL_CPUID, in_buf)
        return struct.unpack("4" + self._pack, out_buf)

    def alloc_phys_mem(self, num_bytes, max_addr):
        in_buf = struct.pack("2" + self._pack, num_bytes, max_addr)
        out_buf = self.ioctl(IOCTL_ALLOC_PHYSMEM, in_buf)
        return struct.unpack("2" + self._pack, out_buf)

    def free_phys_mem(self, physmem):
        in_buf = struct.pack("1" + self._pack, physmem)
        out_buf = self.ioctl(IOCTL_FREE_PHYSMEM, in_buf)
        return struct.unpack("1" + self._pack, out_buf)[0]

    def read_mmio_reg(self, bar_base, size, offset=0, bar_size=None):
        phys_address = bar_base + offset
        in_buf = struct.pack("2" + self._pack, phys_address, size)
        out_buf = self.ioctl(IOCTL_RDMMIO, in_buf)
        reg = out_buf[:size]
        return defines.unpack1(reg, size)

    def write_mmio_reg(self, bar_base, size, value, offset=0, bar_size=None):
        phys_address = bar_base + offset
        in_buf = struct.pack("3" + self._pack, phys_address, size, value)
        out_buf = self.ioctl(IOCTL_WRMMIO, in_buf)

    def get_ACPI_SDT(self):
        raise UnimplementedAPIError("get_ACPI_SDT")

    # ACPI access is implemented through ACPI HAL rather than through kernel module
    def get_ACPI_table(self, table_name):
        raise UnimplementedAPIError("get_ACPI_table")

    #
    # IOSF Message Bus access
    #
    def msgbus_send_read_message(self, mcr, mcrx):
        mdr_out = 0
        in_buf = struct.pack("5" + self._pack, MSGBUS_MDR_OUT_MASK, mcr, mcrx, 0, mdr_out)
        out_buf = self.ioctl(IOCTL_MSGBUS_SEND_MESSAGE, in_buf)
        mdr_out = struct.unpack("5" + self._pack, out_buf)[4]
        return mdr_out

    def msgbus_send_write_message(self, mcr, mcrx, mdr):
        in_buf = struct.pack("5" + self._pack, MSGBUS_MDR_IN_MASK, mcr, mcrx, mdr, 0)
        out_buf = self.ioctl(IOCTL_MSGBUS_SEND_MESSAGE, in_buf)
        return

    def msgbus_send_message(self, mcr, mcrx, mdr=None):
        mdr_out = 0
        if mdr is None:
            in_buf = struct.pack("5" + self._pack, MSGBUS_MDR_OUT_MASK, mcr, mcrx, 0, mdr_out)
        else:
            in_buf = struct.pack("5" + self._pack, (MSGBUS_MDR_IN_MASK | MSGBUS_MDR_OUT_MASK), mcr, mcrx, mdr, mdr_out)
        out_buf = self.ioctl(IOCTL_MSGBUS_SEND_MESSAGE, in_buf)
        mdr_out = struct.unpack("5" + self._pack, out_buf)[4]
        return mdr_out

    #
    # Affinity functions
    #

    def get_affinity(self):
        try:
            affinity = os.sched_getaffinity(0)
            return list(affinity)[0]
        except Exception:
            return None

    def set_affinity(self, thread_id):
        try:
            os.sched_setaffinity(os.getpid(), {thread_id})
            return thread_id
        except Exception:
            return None

    #########################################################
    # (U)EFI Variable API
    #########################################################

    def EFI_supported(self):
        return os.path.exists("/sys/firmware/efi/vars/") or os.path.exists("/sys/firmware/efi/efivars/")

    def delete_EFI_variable(self, name, guid):
        return self.kern_set_EFI_variable(name, guid, "")

    def list_EFI_variables(self):
        return self.kern_list_EFI_variables()

    def get_EFI_variable(self, name, guid, attrs=None):
        return self.kern_get_EFI_variable(name, guid)

    def set_EFI_variable(self, name, guid, data, datasize, attrs=None):
        return self.kern_set_EFI_variable(name, guid, data)

    #
    # Internal (U)EFI Variable API functions via CHIPSEC kernel module
    #

    def kern_get_EFI_variable_full(self, name, guid):
        status_dict = {0: "EFI_SUCCESS",
                       1: "EFI_LOAD_ERROR",
                       2: "EFI_INVALID_PARAMETER",
                       3: "EFI_UNSUPPORTED",
                       4: "EFI_BAD_BUFFER_SIZE",
                       5: "EFI_BUFFER_TOO_SMALL",
                       6: "EFI_NOT_READY",
                       7: "EFI_DEVICE_ERROR",
                       8: "EFI_WRITE_PROTECTED",
                       9: "EFI_OUT_OF_RESOURCES",
                       14: "EFI_NOT_FOUND",
                       26: "EFI_SECURITY_VIOLATION"}
        off = 0
        data = ""
        attr = 0
        buf = list()
        hdr = 0
        base = 12
        namelen = len(name)
        header_size = 52
        data_size = header_size + namelen
        guid0 = int(guid[:8], 16)
        guid1 = int(guid[9:13], 16)
        guid2 = int(guid[14:18], 16)
        guid3 = int(guid[19:21], 16)
        guid4 = int(guid[21:23], 16)
        guid5 = int(guid[24:26], 16)
        guid6 = int(guid[26:28], 16)
        guid7 = int(guid[28:30], 16)
        guid8 = int(guid[30:32], 16)
        guid9 = int(guid[32:34], 16)
        guid10 = int(guid[34:], 16)

        in_buf = struct.pack('13I' + str(namelen) + 's', data_size, guid0, guid1, guid2, guid3, guid4, guid5, guid6, guid7, guid8, guid9, guid10, namelen, name.encode())
        buffer = array.array("B", in_buf)
        stat = self.ioctl(IOCTL_GET_EFIVAR, buffer)
        new_size, status = struct.unpack("2I", buffer[:8])

        if (status == 0x5):
            data_size = new_size + header_size + namelen  # size sent by driver + size of header (size + guid) + size of name
            in_buf = struct.pack('13I' + str(namelen + new_size) + 's', data_size, guid0, guid1, guid2, guid3, guid4, guid5, guid6, guid7, guid8, guid9, guid10, namelen, name.encode())
            buffer = array.array("B", in_buf)
            try:
                stat = self.ioctl(IOCTL_GET_EFIVAR, buffer)
            except IOError:
                logger().log_debug("IOError IOCTL GetUEFIvar\n")
                return (off, buf, hdr, None, guid, attr)
            new_size, status = struct.unpack("2I", buffer[:8])

        if (new_size > data_size):
            logger().log_debug("Incorrect size returned from driver")
            return (off, buf, hdr, None, guid, attr)

        if (status > 0):
            logger().log_debug("Reading variable (GET_EFIVAR) did not succeed: {} ({:d})".format(status_dict.get(status, 'UNKNOWN'), status))
            data = ""
            guid = 0
            attr = 0
        else:
            data = buffer[base:base + new_size].tobytes()
            attr = struct.unpack("I", buffer[8:12])[0]
        return (off, buf, hdr, data, guid, attr)

    def kern_get_EFI_variable(self, name, guid):
        (off, buf, hdr, data, guid, attr) = self.kern_get_EFI_variable_full(name, guid)
        return data

    def kern_list_EFI_variables(self):
        varlist = []
        off = 0
        hdr = 0
        attr = 0
        try:
            if os.path.isdir('/sys/firmware/efi/efivars'):
                varlist = os.listdir('/sys/firmware/efi/efivars')
            elif os.path.isdir('/sys/firmware/efi/vars'):
                varlist = os.listdir('/sys/firmware/efi/vars')
            else:
                return None
        except Exception:
            logger().log_debug('Failed to read /sys/firmware/efi/[vars|efivars]. Folder does not exist')
            return None
        variables = dict()
        for v in varlist:
            name = v[:-37]
            guid = v[len(name) + 1:]
            if name and name is not None:
                variables[name] = []
                var = self.kern_get_EFI_variable_full(name, guid)
                (off, buf, hdr, data, guid, attr) = var
                variables[name].append(var)
        return variables

    def kern_set_EFI_variable(self, name, guid, value, attr=0x7):
        status_dict = {0: "EFI_SUCCESS",
                       1: "EFI_LOAD_ERROR",
                       2: "EFI_INVALID_PARAMETER",
                       3: "EFI_UNSUPPORTED",
                       4: "EFI_BAD_BUFFER_SIZE",
                       5: "EFI_BUFFER_TOO_SMALL",
                       6: "EFI_NOT_READY",
                       7: "EFI_DEVICE_ERROR",
                       8: "EFI_WRITE_PROTECTED",
                       9: "EFI_OUT_OF_RESOURCES",
                       14: "EFI_NOT_FOUND",
                       26: "EFI_SECURITY_VIOLATION"}

        header_size = 60  # 4*15
        namelen = len(name)
        if value:
            datalen = len(value)
        else:
            datalen = 0
            value = struct.pack('B', 0x0)
        data_size = header_size + namelen + datalen
        guid0 = int(guid[:8], 16)
        guid1 = int(guid[9:13], 16)
        guid2 = int(guid[14:18], 16)
        guid3 = int(guid[19:21], 16)
        guid4 = int(guid[21:23], 16)
        guid5 = int(guid[24:26], 16)
        guid6 = int(guid[26:28], 16)
        guid7 = int(guid[28:30], 16)
        guid8 = int(guid[30:32], 16)
        guid9 = int(guid[32:34], 16)
        guid10 = int(guid[34:], 16)

        in_buf = struct.pack('15I' + str(namelen) + 's' + str(datalen) + 's', data_size, guid0, guid1, guid2, guid3, guid4, guid5, guid6, guid7, guid8, guid9, guid10, attr, namelen, datalen, name.encode('utf-8'), value)
        buffer = array.array("B", in_buf)
        stat = self.ioctl(IOCTL_SET_EFIVAR, buffer)
        size, status = struct.unpack("2I", buffer[:8])

        if (status != 0):
            logger().log_debug("Setting EFI (SET_EFIVAR) variable did not succeed: '{}' ({:d})".format(status_dict.get(status, 'UNKNOWN'), status))
        else:
            os.system('umount /sys/firmware/efi/efivars; mount -t efivarfs efivarfs /sys/firmware/efi/efivars')
        return status

    #
    # Hypercalls
    #
    def hypercall(self, rcx, rdx, r8, r9, r10, r11, rax, rbx, rdi, rsi, xmm_buffer):
        in_buf = struct.pack('<11' + self._pack, rcx, rdx, r8, r9, r10, r11, rax, rbx, rdi, rsi, xmm_buffer)
        out_buf = self.ioctl(IOCTL_HYPERCALL, in_buf)
        return struct.unpack('<11' + self._pack, out_buf)[0]

    #
    # Interrupts
    #
    def send_sw_smi(self, cpu_thread_id, SMI_code_data, _rax, _rbx, _rcx, _rdx, _rsi, _rdi):
        self.set_affinity(cpu_thread_id)
        in_buf = struct.pack("7" + self._pack, SMI_code_data, _rax, _rbx, _rcx, _rdx, _rsi, _rdi)
        out_buf = self.ioctl(IOCTL_SWSMI, in_buf)
        ret = struct.unpack("7" + self._pack, out_buf)
        return ret

    #
    # File system
    #
    def get_tool_info(self, tool_type):
        tool_name = _tools[tool_type] if tool_type in _tools else None
        tool_path = os.path.join(get_tools_path(), self.os_system.lower())
        return tool_name, tool_path

    def getcwd(self):
        return os.getcwd()

    def get_page_is_ram(self):
        PROC_KALLSYMS = "/proc/kallsyms"
        symarr = chipsec.file.read_file(PROC_KALLSYMS).splitlines()
        for line in symarr:
            if "page_is_ram" in line:
                return line.split(" ")[0]

    def get_phys_mem_access_prot(self):
        PROC_KALLSYMS = "/proc/kallsyms"
        symarr = chipsec.file.read_file(PROC_KALLSYMS).splitlines()
        for line in symarr:
            if "phys_mem_access_prot" in line:
                return line.split(" ")[0]

    #
    # Logical CPU count
    #
    def get_threads_count(self):
        import multiprocessing
        return multiprocessing.cpu_count()

    #
    # Speculation control
    #
    def retpoline_enabled(self):
        raise UnimplementedAPIError("retpoline_enabled")


def get_helper():
    return LinuxHelper()

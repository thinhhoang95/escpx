import time
import typing

import usb.core
import usb.util

from .exceptions import PrinterNotFound
from .printer import Printer


class UsbPrinter(Printer):
    # Reference printer Epson LX-300+II
    # ID_VENDOR = 0x04b8
    # ID_PRODUCT = 0x0005

    device: usb.core.Device | None

    def __init__(
            self,
            *,
            id_vendor: int,
            id_product: int,
            endpoint_out=0x01,
            endpoint_in=0x82,
            log_io: typing.IO | None = None,
            write_timeout_ms: int = 5000,
            chunk_size: int = 4096,
            inter_chunk_delay_s: float = 0.01,
    ):
        self.device = None
        self.id_vendor = id_vendor
        self.id_product = id_product
        self.endpoint_out = endpoint_out
        self.endpoint_in = endpoint_in
        self.log_io = log_io

        self.write_timeout_ms = write_timeout_ms
        self.chunk_size = chunk_size
        self.inter_chunk_delay_s = inter_chunk_delay_s

        if self.write_timeout_ms <= 0:
            raise ValueError('write_timeout_ms must be > 0')
        if self.chunk_size <= 0:
            raise ValueError('chunk_size must be > 0')
        if self.inter_chunk_delay_s < 0:
            raise ValueError('inter_chunk_delay_s must be >= 0')

        devices = usb.core.show_devices()
        self.log(devices)

        self.device: usb.core.Device = usb.core.find(idVendor=self.id_vendor, idProduct=self.id_product)
        if not self.device:
            hex_value = lambda x: f'0x{x:04x}'
            raise PrinterNotFound(f'USB id_vendor={hex_value(id_vendor)} id_product={hex_value(id_product)}')
        self.log(str(self.device))
        # TODO Check is printer

        self.detach_kernel_driver()

        self.log('Reset device')
        self.device.reset()

    def detach_kernel_driver(self):
        module: str = self.device.backend.__module__
        self.log(f'Module: {module}')
        if module.endswith('libusb1'):
            # TODO Explicit check for errno 13 (permission denied)
            check_driver = None
            try:
                check_driver = self.device.is_kernel_driver_active(interface=0)
            except NotImplementedError as e:
                # Windows libusb backend can report this as unsupported.
                self.log(f'Kernel driver check is not supported on this platform: {e}')
                check_driver = False
            except usb.core.USBError as e:
                self.log(f'Failed to check kernel driver activation: {e}')

            if check_driver is None or check_driver:
                try:
                    self.device.detach_kernel_driver(0)
                except NotImplementedError as e:
                    self.log(f'Kernel driver detach is not supported on this platform: {e}')
                except usb.core.USBError as e:
                    self.log(f'Failed to detach kernel driver: {e}')

    def send(self, sequence: bytes):
        total = len(sequence)
        offset = 0

        while offset < total:
            chunk = sequence[offset: offset + self.chunk_size]
            written = self.device.write(self.endpoint_out, chunk, self.write_timeout_ms)
            if written is None:
                written = len(chunk)
            if written <= 0:
                raise RuntimeError(f'USB write returned {written} at offset {offset}')

            offset += written
            if self.inter_chunk_delay_s > 0:
                time.sleep(self.inter_chunk_delay_s)

    def close(self):
        self.log('Releasing USB')
        if self.device:
            usb.util.dispose_resources(self.device)
        self.device = None
        if self.log_io:
            self.log_io.flush()

    def log(self, message: str):
        if self.log_io:
            self.log_io.write(str(message) + '\n')

    def __del__(self):
        self.close()

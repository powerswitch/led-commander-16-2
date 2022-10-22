#!/usr/bin/env python3

import logging
from logging import error, info

from typing import BinaryIO, Tuple, List, Optional


# File info so far
# 0x00000 - 0x00009  "succeeded", magic number
# 0x00200 - 0x0029F  Block of FF \
# 0x002A0 - 0x002B7  Block of 00 /  Repeating until 0x5AAFF
# 0x5AB00 - 0x5AB53  Names of Channels / Pan / Tilt / Aux
# 0x5AB54 - 0x5AD53  DMX Channel assignment
# 0x5AD54 - 0x5AD83  Block of 00 00 05
# 0x5AD84 - 0x5AD88  "acme\00"
# 0x5AD89 - 0x5AF88  Block of 02
# 0x5AF89 - 0x6A988  Block of 00
# 0x6A989 - 0x6A98C  Random stuff
# 0x6A98D - 0x6A99C  Virtual Dimmer enabled (per fixture)
# 0x6A99D - 0x6AA3C  Virtual Dimmer enabled (per fixture / channel)
# 0x6AA3D - 0x801FF  Block of FF, probably not used


class LedCommanderParser:
    FIXTURES_COUNT: int = 16
    CHANNELS_COUNT: int = 10
    CHANNELS_NAMES_COUNT: int = 12
    DMX_CHANNELS_COUNT: int = 512

    def __init__(self, file):
        self.channel_names = [b"unknown"] * self.CHANNELS_NAMES_COUNT
        self.default_channel_names = self._create_default_channel_names()
        self.dmx_assignments: List[Optional[Tuple[Optional[int], int]]] = [None] * self.DMX_CHANNELS_COUNT
        self.virtual_dimmer_modes: List[int] = [0] * self.FIXTURES_COUNT
        self.virtual_dimmer_assignments: List[List[int]] = [[0] * self.CHANNELS_COUNT] * self.FIXTURES_COUNT

        with open(file, "rb") as readfile:
            self.is_magic_number_ok = self._read_and_check_magic_number(readfile)
            if not self.is_magic_number_ok:
                error("Magic number missing! Abort")
                return
            info(f"Magic number ok")

            self._read_mystery_blocks(readfile)
            self._read_names(readfile)
            self._read_dmx_channel_assignments(readfile)
            self._read_mystery_fixture_info(readfile)
            self.is_magic_number_ok = self._read_acme_info(readfile)
            if not self.is_magic_number_ok:
                error("ACME string missing! Abort")
                return

            self._read_virtual_dimmer_modes(readfile)
            self._read_virtual_dimmer_assignments(readfile)
            self._read_rest(readfile)

    @staticmethod
    def _create_default_channel_names() -> List[str]:
        """
        Create a list of default channel names, incl. pan and tilt as well as aux 1 and aux 2
        :return: List of strings
        """
        return [f"Channel {i + 1}" for i in range(8)] + ["PAN", "TILT", "AUX 1", "AUX 2"]

    def _get_channel_name(self, channel_id: int) -> str:
        """
        Look up custom name of channel 1..10 or aux 1/aux 2. Return custom name for channel or default name if empty.

        :param channel_id: channel to get name for
        :return: string of channel name
        """
        if 0 <= channel_id < 12:
            custom_name: str = self._name_to_str(self.channel_names[channel_id])
            default_name: str = self.default_channel_names[channel_id]

            if custom_name:
                return f"{custom_name} ({default_name})"
            return default_name
        return "<invalid channel id>"

    @staticmethod
    def _read_and_check_magic_number(readfile: BinaryIO) -> bool:
        """
        Read first 512 bytes of file and check if magic string "succeeded" matches.

        First nine bytes contain "succeeded", rest should be \x00.
        Parses file offset 0x00000 - 0x00199

        :param readfile: file to read bytes from.
        :return: True if magic number was read correctly
        """
        magic_number = readfile.read(512)
        return magic_number == b"succeeded" + (b"\x00" * 503)

    @staticmethod
    def _read_mystery_block(readfile: BinaryIO) -> bytes:
        """
        Read block that contain information about scenes and chaser steps.

        TODO: Parsing of block content is still missing, but according to manual,
              it should contain 16 scenes and 2000 chaser steps

        :param readfile: file to read bytes from.
        :return read block
        """
        _guess_payload = readfile.read(160)  # looks like FIXTURES_COUNT * CHANNEL_COUNT to me
        _guess_flags = readfile.read(24)
        # TODO: not sure how to interpret these
        return _guess_payload + _guess_flags

    def _read_mystery_blocks(self, readfile: BinaryIO) -> None:
        """
        Read blocks that contain information about scenes and chaser steps.

        TODO: Parsing of block content is still missing, but according to manual,
              it should contain 16 scenes and 2000 chaser steps
        Parses file offset 0x00200 - 0x5AAFF

        :param readfile: file to read bytes from.
        """
        for block_id in range(2016):  # 16 scenes + 2000 chaser steps
            self._read_mystery_block(readfile)

    @staticmethod
    def _read_name(readfile: BinaryIO) -> bytes:
        """
        Read name of channels, pan, tilt or aux buttons.

        :param readfile: file to read bytes from.
        :return read name in bytes
        """
        name_size: int = 7
        return readfile.read(name_size)

    @staticmethod
    def _name_to_str(raw_name: bytes) -> str:
        """
        Parse raw name into ascii string.

        :param raw_name: read raw name as seven bytes
        :return name as string
        """
        return raw_name.rstrip(b"\x00").decode("ascii", errors="replace")

    def _read_names(self, readfile: BinaryIO) -> None:
        """
        Read names of channels, pan, tilt and aux buttons.

        Parses file offset 0x5AB00 - 0x5AB53

        :param readfile: file to read bytes from.
        """
        for channel_id in range(12):
            channel_name = self._read_name(readfile)
            self.channel_names[channel_id] = channel_name
            info(f"{self.default_channel_names[channel_id]} name: '{self._name_to_str(channel_name)}'")

    def _read_dmx_channel_assignments(self, readfile: BinaryIO) -> None:
        """
        Read assignments of DMX channels to Fixtures+Channel or Aux 1/Aux 2.

        Parses file offset 0x5AB54 - 0x5AD53

        :param readfile: file to read bytes from.
        """
        for dmx_channel in range(self.DMX_CHANNELS_COUNT):
            dmx_channel_assignment = readfile.read(1)[0]
            if dmx_channel_assignment == 0xA2:
                self.dmx_assignments[dmx_channel] = None
            elif dmx_channel_assignment == 0xA1:
                self.dmx_assignments[dmx_channel] = (None, 11)
                info(f"DMX Channel {dmx_channel + 1}: {self._get_channel_name(11)}")
            elif dmx_channel_assignment == 0xA0:
                self.dmx_assignments[dmx_channel] = (None, 10)
                info(f"DMX Channel {dmx_channel + 1}: {self._get_channel_name(10)}")
            else:
                dmx_fixture, dmx_channel_of_fixture = divmod(dmx_channel_assignment, 10)
                self.dmx_assignments[dmx_channel] = (dmx_fixture, dmx_channel_of_fixture)
                info(f"DMX Channel {dmx_channel + 1}: "
                     f"Fixture {dmx_fixture + 1}: "
                     f"{self._get_channel_name(dmx_channel_of_fixture)}")

    def _read_mystery_fixture_info(self, readfile: BinaryIO) -> None:
        """
        Read information about fixtures not known to mankind yet. Appears to be always \x00\x00\x05

        Parses file offset 0x5AD54 - 0x5AD83

        :param readfile: file to read bytes from.
        """
        for fixture_id in range(self.FIXTURES_COUNT):
            readfile.read(3)

    @staticmethod
    def _read_acme_info(readfile: BinaryIO) -> bool:
        """
        Read acme string

        Parses file offset 0x5AD784 - 0x5AD88

        :param readfile: file to read bytes from.
        """
        return readfile.read(5) == b"acme\x00"

    def _read_mystery_dmx_info(self, readfile: BinaryIO) -> None:
        """
        Read information about DMX channels, perhaps. Appears to be always \x02

        Parses file offset 0x5AD89 - 0x5AF88

        :param readfile: file to read bytes from.
        """
        for fixture_id in range(self.DMX_CHANNELS_COUNT):
            readfile.read(1)

    @staticmethod
    def _read_reserved_bytes(readfile: BinaryIO) -> None:
        """
        Read bytes that seem to stay \x00 and thus are hereby declared 'reserved'

        Parses file offset 0x5AF89 - 0x6A988

        :param readfile: file to read bytes from.
        """
        readfile.read(0xFA00)

    @staticmethod
    def _read_random_bytes(readfile: BinaryIO) -> None:
        """
        Read bytes that couldn't be decoded yet.

        Parses file offset 0x6A989 - 0x6A98C

        :param readfile: file to read bytes from.
        """
        readfile.read(3)

    @staticmethod
    def _read_virtual_dimmer_mode(readfile: BinaryIO) -> int:
        """
        Read mode of virtual dimmer, off, RGB or RGBAUVW (Depending on FW>=1.5).

        TODO: Verify and create enum, for now 0 = disabled, else enabled

        :param readfile: file to read bytes from.
        """
        return readfile.read(1)[0]

    def _read_virtual_dimmer_modes(self, readfile: BinaryIO) -> None:
        """
        Read mode of virtual dimmer, off, RGB or RGBAUVW (Depending on FW>=1.5).

        Parses file offset 0x6A98D - 0x6A99C

        :param readfile: file to read bytes from.
        """
        for fixture_id in range(self.FIXTURES_COUNT):
            self.virtual_dimmer_modes[fixture_id] = self._read_virtual_dimmer_mode(readfile)

    @staticmethod
    def _read_virtual_dimmer_assignment(readfile: BinaryIO) -> int:
        """
        Read flag whether to apply virtual dimmer onto channel of fixture.

        :param readfile: file to read bytes from.
        """
        return readfile.read(1)[0]

    def _read_virtual_dimmer_assignments(self, readfile: BinaryIO) -> None:
        """
        Read if virtual dimmer should be applied onto channel of fixture.

        Parses file offset 0x6A99D - 0x6AA3C

        :param readfile: file to read bytes from.
        """
        for fixture_id in range(self.FIXTURES_COUNT):
            for channel_id in range(self.CHANNELS_COUNT):
                self.virtual_dimmer_assignments[fixture_id][channel_id] = self._read_virtual_dimmer_mode(readfile)

    @staticmethod
    def _read_rest(readfile: BinaryIO) -> bool:
        """
        Read rest of file, which should consist only of 0xFF.

        Parses file offset 0x6AA3D - 0x801FF

        :param readfile: file to read bytes from.
        """
        rest = readfile.read()
        return rest == b"\xFF" * len(rest)


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG)

    lcp = LedCommanderParser(sys.argv[1])

#!/usr/bin/env python3
"""Shared RCF / Pure3D AudioFile (RADP/RAD IMA ADPCM) decode helpers.

Used by both the web viewer (server.py: play/download single sounds, and the
batch audio export worker) and the offline CLI tools, so the web chain gets
the same multi-channel support the CLI has had.

RADP layout (headers verified in tools/audio/extract_all_audio.py):
    b'RADP' + <channels u32> <rate u32> <unknown u32> <data_size u32>
    then data_size bytes = data_size/20 blocks.
Each 20-byte block carries 32 4-bit IMA nibbles for ONE channel; blocks are
grouped per-channel: block index = frame_group*channels + channel.
"""
from __future__ import annotations

import re
import struct

# IMA ADPCM step table (89 entries) + nibble index-adjust table, verified
# against the CLI extractor and in-browser playback listening tests.
STEP = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37,
        41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173,
        190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658,
        724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
        2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894,
        6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289,
        16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767]
INDEX = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]

_AUDIOFILE_MARK = b"AudioFile\x00\x02\x00\x00\x00"


def safe_name(s: str) -> str:
    s = re.sub(r'[<>::/\\|?*\x00-\x1f]', '_', s).strip(' .')
    return s or '_'


def parse_audiofiles(data: bytes):
    """Yield AudioFile object dicts found inside one p3d package blob.

    Each record has name/secondary/codec/reserved and, when a RADP payload is
    present and sane: channels/rate/unknown/payload(memoryview). Otherwise an
    'error' field explains why the object was skipped."""
    pos = 0
    while True:
        at = data.find(_AUDIOFILE_MARK, pos)
        if at < 0:
            return
        try:
            p = at + len(_AUDIOFILE_MARK)
            ln = struct.unpack_from('<I', data, p)[0]; p += 4
            if not 0 < ln < 512 or p + ln + 1 > len(data):
                raise ValueError('name')
            name = data[p:p + ln].decode('utf-8', 'replace'); p += ln + 1
            ln2 = struct.unpack_from('<I', data, p)[0]; p += 4
            if ln2 > 4096 or p + ln2 + 1 > len(data):
                raise ValueError('secondary')
            secondary = data[p:p + ln2].decode('utf-8', 'replace'); p += ln2 + 1
            reserved = struct.unpack_from('<I', data, p)[0]; p += 4
            ln3 = struct.unpack_from('<I', data, p)[0]; p += 4
            if not 0 < ln3 < 64 or p + ln3 + 1 > len(data):
                raise ValueError('codec')
            codec = data[p:p + ln3].decode('ascii', 'replace'); p += ln3 + 1
            if codec != 'radp' or data[p:p + 4] != b'RADP':
                yield {'name': name, 'secondary': secondary, 'codec': codec,
                       'reserved': reserved, 'error': 'unsupported codec or missing RADP'}
                pos = at + len(_AUDIOFILE_MARK)
                continue
            channels, rate, unknown, data_size = struct.unpack_from('<IIII', data, p + 4)
            start = p + 20
            end = start + data_size
            if not 1 <= channels <= 32 or not 1000 <= rate <= 384000 or end > len(data) or data_size % 20:
                raise ValueError(f'RADP header channels={channels} rate={rate} size={data_size}')
            yield {'name': name, 'secondary': secondary, 'codec': codec, 'reserved': reserved,
                   'channels': channels, 'rate': rate, 'unknown': unknown,
                   'payload': memoryview(data)[start:end]}
            pos = end
        except (ValueError, struct.error, UnicodeDecodeError) as e:
            yield {'name': f'object_{at:x}', 'codec': 'unknown', 'error': str(e)}
            pos = at + len(_AUDIOFILE_MARK)


def decode_block(block: bytes):
    """One 20-byte block -> 32 s16 samples (single channel)."""
    idx = struct.unpack_from('<h', block, 0)[0]
    hist = struct.unpack_from('<h', block, 2)[0]
    idx = max(0, min(88, idx))
    out = []
    for i in range(32):
        b = block[4 + i // 2]
        n = (b >> (4 if i & 1 else 0)) & 15
        step = STEP[idx]
        delta = step >> 3
        if n & 1: delta += step >> 2
        if n & 2: delta += step >> 1
        if n & 4: delta += step
        if n & 8: delta = -delta
        hist = max(-32768, min(32767, hist + delta))
        idx = max(0, min(88, idx + INDEX[n]))
        out.append(hist)
    return out


def decode_frames(payload, channels: int) -> list:
    """Whole RADP payload -> interleaved s16 sample list (wav frame order)."""
    blocks = len(payload) // 20
    if channels <= 0 or blocks % channels:
        raise ValueError('RADP block count is not divisible by channels')
    pcm = []
    for base in range(0, blocks, channels):
        decoded = [decode_block(payload[(base + c) * 20:(base + c + 1) * 20]) for c in range(channels)]
        for frame in range(32):
            for c in range(channels):
                pcm.append(decoded[c][frame])
    return pcm


def duration_seconds(payload_len: int, channels: int, rate: int) -> float:
    return (payload_len // 20 * 32) / (rate * channels)


def wav_bytes(rate: int, channels: int, pcm_samples) -> bytes:
    """Minimal PCM16 .wav container (interleaved little-endian s16)."""
    data = struct.pack('<%dh' % len(pcm_samples), *pcm_samples)
    byte_rate = rate * channels * 2
    block_align = channels * 2
    fmt = struct.pack('<HHIIHH', 1, channels, rate, byte_rate, block_align, 16)
    return (b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE'
            + b'fmt ' + struct.pack('<I', 16) + fmt
            + b'data' + struct.pack('<I', len(data)) + data)

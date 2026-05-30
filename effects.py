"""Realtime audio-effect state + ffmpeg filter-chain building for Shehrazad.

ffmpeg audio filters are fixed when the process spawns, so changing an effect
mid-song means re-spawning ffmpeg seeked to the current position. This module
just holds the desired effect state and renders it into ffmpeg `-af` options;
the respawn/seek logic lives in shehrazad.py.
"""

DISCORD_SAMPLE_RATE = 48000


def _atempo_chain(factor):
    """ffmpeg's atempo accepts 0.5..2.0 per stage; chain stages for the rest."""
    factor = max(0.25, min(4.0, float(factor)))
    stages = []
    remaining = factor
    while remaining > 2.0:
        stages.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        stages.append("atempo=0.5")
        remaining /= 0.5
    stages.append(f"atempo={remaining:.4f}")
    return stages


class EffectState:
    """Mutable bundle of the currently-requested audio effects."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.normalize = True   # loudnorm on/off
        self.bass = 0           # dB gain (0 = off)
        self.treble = 0         # dB gain (0 = off)
        self.speed = 1.0        # tempo only, pitch preserved
        self.pitch = 1.0        # >1 nightcore, <1 vaporwave (speed + pitch)
        self.reverb = False
        self.eight_d = False    # rotating "8D" pan effect

    def active(self):
        return bool(
            self.bass or self.treble or self.speed != 1.0
            or self.pitch != 1.0 or self.reverb or self.eight_d
        )

    def to_filters(self):
        f = []
        if self.normalize:
            f.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if self.bass:
            f.append(f"bass=g={self.bass}")
        if self.treble:
            f.append(f"treble=g={self.treble}")
        if self.pitch != 1.0:
            f.append(f"asetrate={DISCORD_SAMPLE_RATE}*{self.pitch}")
            f.append(f"aresample={DISCORD_SAMPLE_RATE}")
        if self.speed != 1.0:
            f.extend(_atempo_chain(self.speed))
        if self.eight_d:
            f.append("apulsator=hz=0.09")
        if self.reverb:
            f.append("aecho=0.8:0.9:1000:0.3")
        # Always finish at Discord's expected rate.
        f.append(f"aresample={DISCORD_SAMPLE_RATE}")
        return f

    def to_options(self):
        """Full ffmpeg output-side options string for FFmpegPCMAudio."""
        opts = "-vn"
        filters = self.to_filters()
        if filters:
            opts += ' -af "' + ",".join(filters) + '"'
        opts += f" -ar {DISCORD_SAMPLE_RATE} -ac 2"
        return opts

    def describe(self):
        parts = [f"تطبيع: {'تشغيل' if self.normalize else 'إيقاف'}"]
        if self.bass:
            parts.append(f"باس: {self.bass:+d}dB")
        if self.treble:
            parts.append(f"تربل: {self.treble:+d}dB")
        if self.speed != 1.0:
            parts.append(f"سرعة: {self.speed:g}x")
        if self.pitch != 1.0:
            label = "نايتكور" if self.pitch > 1 else "فيبورويف"
            parts.append(f"{label}: {self.pitch:g}x")
        if self.reverb:
            parts.append("ريفيرب")
        if self.eight_d:
            parts.append("8D")
        return " | ".join(parts)

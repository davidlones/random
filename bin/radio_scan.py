#!/home/david/.venvs/radio/bin/python
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import osmosdr
from gnuradio import analog, blocks, filter, gr
from gnuradio.fft import window
from gnuradio.filter import firdes


@dataclass
class ScanResult:
    mhz: float
    score: float
    rms: float
    pilot_db: float


class CaptureAudio(gr.top_block):
    def __init__(self, frequency_hz: float, seconds: float) -> None:
        super().__init__("hackrf-wfm-scan")

        sample_rate = 2_000_000
        quadrature_rate = 250_000
        audio_rate = 50_000
        decimation = int(sample_rate // quadrature_rate)
        tune_offset = 250_000
        sample_count = int(audio_rate * seconds)

        source = osmosdr.source(args="numchan=1 hackrf=0")
        source.set_sample_rate(sample_rate)
        source.set_center_freq(frequency_hz + tune_offset)
        source.set_freq_corr(0)
        source.set_dc_offset_mode(1)
        source.set_iq_balance_mode(0)
        source.set_gain_mode(False)
        source.set_gain(0, "AMP")
        source.set_gain(32, "LNA")
        source.set_gain(40, "VGA")
        source.set_bandwidth(1_500_000)

        channel_filter = filter.freq_xlating_fir_filter_ccf(
            decimation,
            firdes.low_pass(
                1.0,
                sample_rate,
                100_000,
                50_000,
                window.WIN_HAMMING,
            ),
            tune_offset,
            sample_rate,
        )
        demod = analog.wfm_rcv(quad_rate=quadrature_rate, audio_decimation=5)
        head = blocks.head(gr.sizeof_float, sample_count)
        sink = blocks.vector_sink_f()

        self.connect(source, channel_filter, demod, head, sink)
        self.sink = sink


def score_audio(samples: np.ndarray, sample_rate: int) -> tuple[float, float, float]:
    if samples.size == 0:
        return -1e9, 0.0, -120.0

    samples = samples.astype(np.float32)
    samples -= np.mean(samples)
    rms = float(np.sqrt(np.mean(samples * samples)))

    n = min(65536, samples.size)
    segment = samples[:n]
    win = np.hanning(n)
    spectrum = np.fft.rfft(segment * win)
    freqs = np.fft.rfftfreq(n, d=1 / sample_rate)
    mags = np.abs(spectrum) + 1e-12

    band_mask = (freqs >= 200) & (freqs <= 16000)
    noise_floor = float(np.median(20 * np.log10(mags[band_mask])))

    pilot_mask = (freqs >= 18500) & (freqs <= 19500)
    pilot_db = float(np.max(20 * np.log10(mags[pilot_mask])))

    mid_mask = (freqs >= 1000) & (freqs <= 12000)
    mid_db = float(np.mean(20 * np.log10(mags[mid_mask])))

    score = (mid_db - noise_floor) + 0.35 * (pilot_db - noise_floor) + 40.0 * rms
    return score, rms, pilot_db


def scan_frequency(mhz: float, seconds: float) -> ScanResult:
    tb = CaptureAudio(mhz * 1_000_000, seconds)
    tb.start()
    tb.wait()
    samples = np.array(tb.sink.data(), dtype=np.float32)
    score, rms, pilot_db = score_audio(samples, 50_000)
    return ScanResult(mhz=mhz, score=score, rms=rms, pilot_db=pilot_db)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan nearby FM broadcast frequencies with a HackRF.")
    parser.add_argument("--center", type=float, required=True, help="Center frequency in MHz.")
    parser.add_argument("--span", type=float, default=0.3, help="Total span in MHz.")
    parser.add_argument("--step", type=float, default=0.025, help="Step size in MHz.")
    parser.add_argument("--seconds", type=float, default=0.8, help="Audio capture length per step.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = args.center - args.span / 2.0
    stop = args.center + args.span / 2.0
    freqs = np.arange(start, stop + args.step / 2.0, args.step)

    results: list[ScanResult] = []
    for mhz in freqs:
        result = scan_frequency(round(float(mhz), 6), args.seconds)
        results.append(result)
        print(
            f"{result.mhz:8.3f} MHz  score={result.score:7.2f}  rms={result.rms:0.4f}  pilot={result.pilot_db:6.1f} dB",
            flush=True,
        )

    print("\nBest candidates:", flush=True)
    for result in sorted(results, key=lambda item: item.score, reverse=True)[:5]:
        print(
            f"{result.mhz:8.3f} MHz  score={result.score:7.2f}  rms={result.rms:0.4f}  pilot={result.pilot_db:6.1f} dB"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

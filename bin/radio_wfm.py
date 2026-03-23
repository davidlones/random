#!/home/david/.venvs/radio/bin/python
from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

import osmosdr
from gnuradio import analog, audio, blocks, filter, gr
from gnuradio.fft import window
from gnuradio.filter import firdes


STATE_DIR = Path.home() / ".local" / "state" / "radio"


class HackRFWfm(gr.top_block):
    def __init__(self, frequency_hz: float, volume: float, audio_device: str = "") -> None:
        super().__init__("hackrf-wfm")

        sample_rate = 2_000_000
        quadrature_rate = 250_000
        audio_rate = 50_000
        output_rate = 48_000
        decimation = int(sample_rate // quadrature_rate)
        tune_offset = 250_000

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
        demod = analog.wfm_rcv(
            quad_rate=quadrature_rate,
            audio_decimation=5,
        )
        level = blocks.multiply_const_ff(volume)
        audio_resampler = filter.rational_resampler_fff(
            interpolation=24,
            decimation=25,
        )
        sink = audio.sink(output_rate, audio_device, True)

        self.connect(source, channel_filter, demod, level, audio_resampler, sink)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play broadcast FM from a HackRF in the background.")
    parser.add_argument(
        "--frequency",
        type=float,
        required=True,
        help="Frequency in MHz, for example 98.7",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=2.5,
        help="Linear volume multiplier after demodulation.",
    )
    parser.add_argument(
        "--audio-device",
        default="",
        help="Optional GNU Radio audio device name.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    tb = HackRFWfm(
        frequency_hz=args.frequency * 1_000_000,
        volume=args.volume,
        audio_device=args.audio_device,
    )

    def handle_signal(_signum: int, _frame: object) -> None:
        tb.stop()
        tb.wait()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print(f"Starting HackRF WFM receiver on {args.frequency:.1f} MHz", flush=True)
    tb.start()
    try:
        signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        tb.stop()
        tb.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())

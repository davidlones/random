#!/home/david/.venvs/radio/bin/python
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from typing import Any

import osmosdr
import pmt
import rds
from gnuradio import analog, audio, blocks, digital, filter, gr
from gnuradio.filter import firdes


MESSAGE_KEYS = {
    0: "program_information",
    1: "station_name",
    2: "program_type",
    3: "flags",
    4: "radiotext",
    5: "clock_time",
    6: "alt_frequencies",
    7: "tuned_frequency",
}


class RdsMessageSink(gr.sync_block):
    def __init__(self, frequency_mhz: float, once: bool = False) -> None:
        super().__init__("rds-message-sink", in_sig=None, out_sig=None)
        self.frequency_mhz = frequency_mhz
        self.once = once
        self.state: dict[str, Any] = {"frequency_mhz": frequency_mhz}
        self.last_values: dict[str, str] = {}
        self.message_port_register_in(pmt.intern("in"))
        self.set_msg_handler(pmt.intern("in"), self.handle_msg)

    def handle_msg(self, msg: pmt.pmt_base) -> None:
        if not pmt.is_tuple(msg):
            return
        msg_type = pmt.to_long(pmt.tuple_ref(msg, 0))
        value = pmt.symbol_to_string(pmt.tuple_ref(msg, 1)).rstrip()
        key = MESSAGE_KEYS.get(msg_type, f"msg_{msg_type}")
        if self.last_values.get(key) == value:
            return
        self.last_values[key] = value
        self.state[key] = value
        print(json.dumps({"event": key, "value": value, "state": self.state}, sort_keys=True), flush=True)
        if self.once and key in {"station_name", "radiotext"} and value.strip():
            raise SystemExit(0)


class HackRdsReceiver(gr.top_block):
    def __init__(self, frequency_mhz: float, volume_db: float, audio_device: str, sink: RdsMessageSink) -> None:
        super().__init__("hackrf-rds-receiver")

        samp_rate = 1_920_000
        decimation = 6
        freq_offset = 250_000
        quad_rate = samp_rate / decimation
        audio_rate = 240_000

        source = osmosdr.source(args="numchan=1 hackrf=0")
        source.set_sample_rate(samp_rate)
        source.set_center_freq(frequency_mhz * 1e6 - freq_offset)
        source.set_freq_corr(0)
        source.set_dc_offset_mode(0)
        source.set_iq_balance_mode(0)
        source.set_gain_mode(False)
        source.set_gain(25, 0)
        source.set_if_gain(20, 0)
        source.set_bb_gain(20, 0)
        source.set_bandwidth(0)

        chan = filter.freq_xlating_fir_filter_ccc(
            decimation,
            firdes.low_pass(1, samp_rate, 135000, 20000),
            freq_offset,
            samp_rate,
        )
        fm = analog.quadrature_demod_cf(quad_rate / (2 * 3.141592653589793 * 75000))

        # Mono audio path, enough to listen while RDS is being decoded.
        mono_resamp = filter.rational_resampler_fff(interpolation=240000, decimation=int(quad_rate))
        mono_delay = blocks.delay(gr.sizeof_float, 20)
        mono_lpr = filter.fir_filter_fff(5, firdes.low_pass(1.0, 240000, 15e3, 2e3))
        deemph = analog.fm_deemph(48000, 75e-6)
        volume = blocks.multiply_const_ff(10 ** (volume_db / 10.0))
        audio_out = audio.sink(48000, audio_device, True)

        # RDS chain taken from the upstream gr-rds receiver example, minus the GUI.
        rds_xlate = filter.freq_xlating_fir_filter_fcc(
            10,
            firdes.low_pass(1.0, quad_rate, 7.5e3, 5e3),
            57e3,
            quad_rate,
        )
        rds_resamp = filter.rational_resampler_ccc(interpolation=19000, decimation=int(quad_rate / 10))
        rrc_taps = firdes.root_raised_cosine(1.0, 19000, 19000 / 8, 1.0, 151)
        rrc_taps_manchester = [rrc_taps[n] - rrc_taps[n + 8] for n in range(len(rrc_taps) - 8)]
        rrc = filter.fir_filter_ccc(1, rrc_taps_manchester)
        agc = analog.agc_cc(2e-3, 0.585, 53)
        agc.set_max_gain(1000)
        symbol_sync = digital.symbol_sync_cc(
            digital.TED_ZERO_CROSSING,
            16,
            0.01,
            1.0,
            1.0,
            0.1,
            1,
            digital.constellation_bpsk().base(),
            digital.IR_MMSE_8TAP,
            128,
            [],
        )
        constellation = digital.constellation_receiver_cb(
            digital.constellation_bpsk().base(),
            2 * 3.141592653589793 / 100,
            -0.002,
            0.002,
        )
        diff = digital.diff_decoder_bb(2, digital.DIFF_DIFFERENTIAL)
        decoder = rds.decoder(False, False)
        parser = rds.parser(False, False, 1)
        parser.reset()

        null0 = blocks.null_sink(gr.sizeof_float)
        null1 = blocks.null_sink(gr.sizeof_float)
        null2 = blocks.null_sink(gr.sizeof_float)

        self.connect(source, chan, fm)
        self.connect(fm, mono_resamp, mono_delay, mono_lpr, deemph, volume, audio_out)
        # RDS decode chain
        self.connect(fm, rds_xlate, rds_resamp, rrc, agc, symbol_sync, constellation, diff, decoder)
        self.connect((constellation, 1), null0)
        self.connect((constellation, 2), null1)
        self.connect((constellation, 3), null2)
        self.msg_connect((decoder, "out"), (parser, "in"))
        self.msg_connect((parser, "out"), (sink, "in"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Decode FM RDS/RBDS metadata with a HackRF.")
    parser.add_argument("--frequency", type=float, required=True, help="FM frequency in MHz, for example 103.7")
    parser.add_argument("--volume-db", type=float, default=-6.0, help="Audio gain in dB for the monitor output.")
    parser.add_argument("--audio-device", default="", help="Optional GNU Radio audio device name.")
    parser.add_argument("--seconds", type=float, default=0.0, help="Stop after N seconds; 0 means run until interrupted.")
    parser.add_argument("--once", action="store_true", help="Exit once station name or radiotext appears.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sink = RdsMessageSink(args.frequency, once=args.once)
    tb = HackRdsReceiver(args.frequency, args.volume_db, args.audio_device, sink)

    def stop(_signum: int, _frame: object) -> None:
        tb.stop()
        tb.wait()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    tb.start()
    try:
        if args.seconds > 0:
            time.sleep(args.seconds)
        else:
            signal.pause()
    finally:
        tb.stop()
        tb.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

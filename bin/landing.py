# file: sol_launchpad.py
# usage:
#   python sol_launchpad.py --sheet "https://docs.google.com/spreadsheets/d/XXXX" --port 8000
#
# Serves a neon-green landing page with a link to the sheet and a WebAudio beep sequence.
# No external audio files required.

import argparse
from flask import Flask, render_template_string

HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Sol • Task Launchpad</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root { --bg:#000; --fg:#00ff00; --fg-dim:#00cc00; --accent:#00ff7f; }
    html, body { height:100%; margin:0; background:var(--bg); color:var(--fg);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
    .wrap { min-height:100%; display:grid; place-items:center; padding:2rem; }
    .panel { width:min(900px,95vw); border:1px solid var(--fg-dim); border-radius:10px;
      padding:2rem 2.25rem; box-shadow:0 0 30px rgba(0,255,0,.08), inset 0 0 60px rgba(0,255,0,.05); }
    h1 { margin:0 0 .75rem 0; font-size:clamp(22px,3vw,32px); letter-spacing:.02em; color:var(--accent);
      text-shadow:0 0 6px rgba(0,255,127,.6); }
    p { margin:.35rem 0; line-height:1.55; color:var(--fg); }
    .link { display:inline-block; margin-top:1rem; padding:.9rem 1.1rem; border:1px solid var(--fg);
      border-radius:8px; text-decoration:none; color:var(--bg); background:var(--fg); font-weight:700;
      box-shadow:0 0 12px rgba(0,255,0,.35); }
    .link:hover { filter:brightness(1.15); }
    .small { color:var(--fg-dim); font-size:.9rem; margin-top:.75rem; }
    .status { margin-top:.75rem; font-size:.95rem; color:var(--fg-dim); white-space:pre-line; }
    .controls { margin-top: .9rem; display:flex; gap:.75rem; flex-wrap:wrap; align-items:center; }
    .btn { padding:.55rem .9rem; border:1px solid var(--fg-dim); color:var(--fg); background:transparent;
      border-radius:8px; cursor:pointer; }
    .btn:hover { border-color:var(--fg); }
    .overlay { position:fixed; inset:0; display:none; background:rgba(0,0,0,.85); color:var(--fg);
      align-items:center; justify-content:center; text-align:center; padding:2rem; }
    .overlay button { margin-top:1rem; padding:.7rem 1rem; background:var(--fg); border:none;
      color:var(--bg); border-radius:8px; font-weight:700; cursor:pointer; }
    input[type=range] { accent-color: var(--fg); }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1>Sol // Entropy Mitigation Launchpad</h1>
      <p>Housemates: click the link below to open the live checklist. Complete <strong>one task today</strong>. Mark it there. Keep the momentum.</p>
      <p><a class="link" href="{{ sheet_url }}" target="_blank" rel="noopener">Open the Google Spreadsheet</a></p>
      <div class="controls">
        <button class="btn" id="play">▶ Play Beeps</button>
        <button class="btn" id="stop">■ Stop</button>
        <label>Volume
          <input id="vol" type="range" min="0" max="100" value="80" />
        </label>
      </div>
      <p class="small">Tip: add the sheet to your phone’s home screen for faster taps.</p>
      <div class="status" id="status">Ready.</div>
    </div>
  </div>

  <div class="overlay" id="overlay">
    <div>
      <p>Tap to initialize audio and play the brief.</p>
      <button id="unlock">Initialize & Play</button>
    </div>
  </div>

  <script>
    // ===== WebAudio Beep Engine =====
    let ctx, master, osc;
    let playing = false;
    let stopRequested = false;

    // Simple “mission motif”: [frequency Hz, duration ms, gapAfter ms]
    // You can tweak this sequence to taste.
    const motif = [
      [880, 160, 90],
      [660, 120, 120],
      [990, 180, 160],
      [0,   120, 60],  // rest
      [880, 220, 240],
      // tag: a quick “done” chirp
      [1560, 60, 60],
      [1760, 60, 0]
    ];

    function log(s){ const el = document.getElementById('status'); el.textContent = s; console.log(s); }

    async function ensureContext() {
      if (!ctx) {
        ctx = new (window.AudioContext || window.webkitAudioContext)();
        master = ctx.createGain();
        master.gain.value = document.getElementById('vol').value / 100 * 0.9;
        master.connect(ctx.destination);
      } else if (ctx.state === 'suspended') {
        await ctx.resume();
      }
    }

    function setVolume() {
      if (master) {
        const v = document.getElementById('vol').value / 100;
        master.gain.setTargetAtTime(v * 0.9, ctx.currentTime, 0.01);
      }
    }

    function tone(freq, ms) {
      return new Promise(resolve => {
        if (stopRequested) return resolve();
        if (freq <= 0) { // rest
          setTimeout(resolve, ms);
          return;
        }
        osc = ctx.createOscillator();
        const g = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.setValueAtTime(freq, ctx.currentTime);
        // quick attack/decay envelope (avoids clicks)
        g.gain.setValueAtTime(0.0001, ctx.currentTime);
        g.gain.exponentialRampToValueAtTime(master.gain.value, ctx.currentTime + 0.01);
        g.gain.setTargetAtTime(0.0001, ctx.currentTime + ms/1000 - 0.02, 0.015);

        osc.connect(g).connect(master);
        osc.start();

        setTimeout(() => {
          try { osc.stop(); } catch(e){}
          resolve();
        }, ms);
      });
    }

    function gap(ms) {
      return new Promise(resolve => setTimeout(resolve, ms));
    }

    async function playMotif() {
      if (playing) return;
      stopRequested = false;
      playing = true;
      log("Playing Sol’s brief (beeps)…");
      try {
        for (const [f, d, g] of motif) {
          if (stopRequested) break;
          await tone(f, d);
          if (g) await gap(g);
        }
        if (!stopRequested) log("Brief finished.");
      } catch (e) {
        console.error(e);
        log("Playback error. Try again.");
      } finally {
        playing = false;
      }
    }

    function stopAll() {
      stopRequested = true;
      if (osc) { try { osc.stop(); } catch(e){} }
      log("Stopped.");
    }

    // ===== UI & Mobile Unlock =====
    const overlay = document.getElementById('overlay');
    const unlockBtn = document.getElementById('unlock');
    const playBtn = document.getElementById('play');
    const stopBtn = document.getElementById('stop');
    const vol = document.getElementById('vol');

    // Try autoplay on load; most mobile browsers will block until a tap.
    window.addEventListener('load', async () => {
      try {
        await ensureContext();
        // Attempt a muted chirp to prewarm; many browsers still block, so we show overlay.
        overlay.style.display = 'flex';
      } catch {
        overlay.style.display = 'flex';
      }
    });

    unlockBtn.addEventListener('click', async () => {
      overlay.style.display = 'none';
      await ensureContext();
      await playMotif();
    });

    playBtn.addEventListener('click', async () => {
      await ensureContext();
      await playMotif();
    });

    stopBtn.addEventListener('click', stopAll);
    vol.addEventListener('input', setVolume);
  </script>
</body>
</html>
"""

def create_app(sheet_url: str):
    app = Flask(__name__, static_folder=None)

    @app.route("/")
    def index():
        return render_template_string(HTML, sheet_url=sheet_url)

    return app

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Serve a hacker-green launch page with WebAudio beeps.")
    parser.add_argument("--sheet", required=True, help="Google Sheets URL to open.")
    parser.add_argument("--port", type=int, default=8000, help="Port to serve on (default 8000).")
    args = parser.parse_args()

    app = create_app(args.sheet)
    app.run(host="0.0.0.0", port=args.port, debug=False)

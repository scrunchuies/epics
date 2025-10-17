# app.py
import json, threading, time, math, glob
from datetime import datetime, date
from flask import Flask, jsonify, render_template_string, send_from_directory, request
import os
import serial

BAUD = 115200
SERIAL_SCAN = ["/dev/ttyACM*", "/dev/ttyUSB*"]

latest_raw, latest_ts = None, None
last_err, cur_port = None, None

HIST_MAX = 300
# persistent daily CSV logging
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
# keep both human label and epoch seconds for reliable charting
hist = {"labels": [], "ts": [], "avgF": [], "avgH": []}  # labels are HH:MM:SS strings

def load_hist_from_csv(day_str=None):
    try:
        if day_str is None:
            day_str = datetime.now().strftime('%Y-%m-%d')
        fp = os.path.join(LOG_DIR, f"{day_str}.csv")
        if not os.path.exists(fp):
            return
        labels, ts_list, avgF_list, avgH_list = [], [], [], []
        with open(fp, 'r', encoding='utf-8') as f:
            next(f, None)
            for line in f:
                parts = line.strip().split(',')
                if not parts or len(parts) < 4:
                    continue
                try:
                    epoch_s = int(parts[0])
                except Exception:
                    continue
                ts_list.append(epoch_s)
                labels.append(datetime.fromtimestamp(epoch_s).strftime('%H:%M:%S'))
                try:
                    af = float(parts[2]) if parts[2] != '' else None
                except Exception:
                    af = None
                try:
                    ah = float(parts[3]) if parts[3] != '' else None
                except Exception:
                    ah = None
                avgF_list.append(af)
                avgH_list.append(ah)
        # keep only last HIST_MAX
        labels = labels[-HIST_MAX:]
        ts_list = ts_list[-HIST_MAX:]
        avgF_list = avgF_list[-HIST_MAX:]
        avgH_list = avgH_list[-HIST_MAX:]
        hist["labels"] = labels
        hist["ts"] = ts_list
        hist["avgF"] = avgF_list
        hist["avgH"] = avgH_list
    except Exception:
        pass

def find_port():
    for pat in SERIAL_SCAN:
        for p in sorted(glob.glob(pat)):
            try:
                s = serial.Serial(p, BAUD, timeout=1)
                s.reset_input_buffer()
                return s, p
            except Exception:
                pass
    return None, None

def reader():
    global latest_raw, latest_ts, last_err, cur_port
    ser, cur_port = find_port()
    while True:
        if ser is None:
            last_err = "No serial device found"
            time.sleep(2)
            ser, cur_port = find_port()
            continue
        try:
            line = ser.readline().decode("utf-8", "ignore").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)  # expects F22,FA,FB,H22,HA,HB,AvgF,AvgH
                latest_raw, latest_ts = obj, time.time()
                last_err = None
                af, ah = obj.get("AvgF"), obj.get("AvgH")
                if isinstance(af,(int,float)) and not math.isnan(af):
                    now_ts = int(latest_ts)
                    hist["labels"].append(datetime.now().strftime("%H:%M:%S"))
                    hist["ts"].append(now_ts)
                    hist["avgF"].append(float(af))
                    hist["avgH"].append(float(ah) if isinstance(ah,(int,float)) and not math.isnan(ah) else None)
                    for k in hist: hist[k] = hist[k][-HIST_MAX:]
                    # append to daily CSV
                    try:
                        day = datetime.fromtimestamp(now_ts).strftime('%Y-%m-%d')
                        fp = os.path.join(LOG_DIR, f"{day}.csv")
                        is_new = not os.path.exists(fp)
                        with open(fp, 'a', encoding='utf-8') as f:
                            if is_new:
                                f.write('epoch_s,iso,avgF,avgH\n')
                            iso_ts = datetime.utcfromtimestamp(now_ts).isoformat()+"Z"
                            f.write(f"{now_ts},{iso_ts},{float(af) if af is not None else ''},{float(ah) if (isinstance(ah,(int,float)) and not math.isnan(ah)) else ''}\n")
                    except Exception:
                        pass
            except json.JSONDecodeError:
                last_err = "Bad JSON from serial"
        except serial.SerialException as e:
            last_err = f"Serial error: {e}"
            try: ser.close()
            except Exception: pass
            ser, cur_port = None, None
            time.sleep(2)

def safe(v):
    return None if v is None or (isinstance(v,float) and math.isnan(v)) else v

def payload():
    raw, now = latest_raw or {}, latest_ts
    sensors = [
        {"id":"DHT22",  "temp_f":safe(raw.get("F22")), "humidity":safe(raw.get("H22"))},
        {"id":"DHT11A", "temp_f":safe(raw.get("FA")),  "humidity":safe(raw.get("HA"))},
        {"id":"DHT11B", "temp_f":safe(raw.get("FB")),  "humidity":safe(raw.get("HB"))},
    ]
    return {
        "updated": {
            "epoch": now,
            "iso":   datetime.utcfromtimestamp(now).isoformat()+"Z" if now else None,
            "local": datetime.fromtimestamp(now).strftime("%Y-%m-%d %I:%M:%S %p") if now else None,
            "ago":   (f"{int(time.time()-now)}s ago" if now else None)
        },
        "averages": {"temp_f": safe(raw.get("AvgF")), "humidity": safe(raw.get("AvgH"))},
        "sensors": sensors,
        "status": {"port": cur_port, "error": last_err}
    }

app = Flask(__name__)

@app.route('/favicon.png')
def favicon():
    return send_from_directory('/home/kura', 'favicon.png', mimetype='image/png')

@app.route("/data")
def data(): return jsonify(payload())

@app.route("/history")
def history():
    q_date = request.args.get('date')
    if q_date:
        try:
            _ = datetime.strptime(q_date, '%Y-%m-%d')
            fp = os.path.join(LOG_DIR, f"{q_date}.csv")
            if not os.path.exists(fp):
                return jsonify({"labels": [], "ts": [], "avgF": [], "avgH": []})
            labels, ts_list, avgF_list, avgH_list = [], [], [], []
            with open(fp, 'r', encoding='utf-8') as f:
                next(f, None)
                for line in f:
                    parts = line.strip().split(',')
                    if not parts or len(parts) < 4:
                        continue
                    try:
                        epoch_s = int(parts[0])
                    except Exception:
                        continue
                    ts_list.append(epoch_s)
                    labels.append(datetime.fromtimestamp(epoch_s).strftime('%H:%M:%S'))
                    try:
                        af = float(parts[2]) if parts[2] != '' else None
                    except Exception:
                        af = None
                    try:
                        ah = float(parts[3]) if parts[3] != '' else None
                    except Exception:
                        ah = None
                    avgF_list.append(af)
                    avgH_list.append(ah)
            return jsonify({"labels": labels, "ts": ts_list, "avgF": avgF_list, "avgH": avgH_list})
        except ValueError:
            return jsonify({"labels": [], "ts": [], "avgF": [], "avgH": []})
    return jsonify(hist)

TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard</title>

<link rel="icon" type="image/x-icon" href="/favicon.png">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<style>
:root{
  --bg:#0b1220;--card:#0f1724;--grid:#243147;--muted:#9aa3b2;--accent:#7cc8ff;--good:#77d28d;--warn:#ffc857;--bad:#ff7b7b;
  --glass: rgba(255,255,255,0.02);
}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:#e9eef8;font-family:Inter,ui-sans-serif,system-ui,Segoe UI,Roboto,Helvetica,Arial}
.header{display:flex;align-items:center;justify-content:space-between;padding:18px 20px;border-bottom:1px solid var(--grid)}
.title{display:flex;gap:12px;align-items:center}
h1{font-size:18px;margin:0}.subtitle{color:var(--muted);font-size:13px}
.container{max-width:1200px;margin:22px auto;padding:0 18px}
.controls{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-bottom:16px}
.control-card{background:var(--card);border-radius:12px;padding:10px 12px;border:1px solid var(--grid);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.switch{display:inline-flex;gap:8px;align-items:center}
.btn{background:transparent;border:1px solid transparent;padding:6px 10px;border-radius:8px;cursor:pointer;color:var(--muted)}
.btn.active{border-color:var(--accent);color:var(--accent);background:linear-gradient(180deg,rgba(124,200,255,0.04),transparent)}
.select{background:#0c1424;border:1px solid var(--grid);color:#cfd6ff;border-radius:8px;padding:6px 8px}
.card-grid{display:grid;grid-template-columns:1.2fr 1fr;gap:16px;align-items:start}
.left-col{display:flex;flex-direction:column;gap:16px}
.card{background:var(--card);border-radius:12px;padding:14px;border:1px solid var(--grid);box-shadow:0 6px 20px rgba(0,0,0,.25)}
.small{font-size:13px;color:var(--muted)}
.row{display:flex;justify-content:space-between;align-items:center;margin-top:6px}
.big{font-size:34px;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:8px;align-items:stretch}
.grid > .card{height:100%}
.tag{font-size:12px;padding:6px 10px;border-radius:999px;background:var(--glass);color:var(--muted)}
.alert{background:#3a1f1f;border:1px solid #5a2d2d;color:#ffd6d6;border-radius:10px;padding:10px;margin:12px 0}
.footer{text-align:center;color:var(--muted);font-size:12px;padding:18px;margin-top:8px}
.kv{color:var(--muted);font-size:13px}
.footer a{color:var(--accent);text-decoration:none}
@media (max-width:900px){ .card-grid{grid-template-columns:1fr} .big{font-size:28px}}
.chart-box{ position:relative; height:260px; max-height:36vh; min-height:180px; width:100%}
#chart{ position:absolute; inset:0; width:100%!important; height:100%!important; border-radius:10px; background:linear-gradient(180deg,rgba(255,255,255,.01),transparent)}

/* compact controls (cleaner chart bar) */
.control-card.controls-bar{align-items:flex-end;gap:14px;padding:10px 12px 10px;flex:1 1 100%;margin-left:0}
.field{display:flex;flex-direction:column;gap:6px;min-width:140px}
.field>span{font-size:12px;color:var(--muted)}
.unit-badge{display:inline-block;font-size:12px;padding:4px 8px;border-radius:999px;background:var(--glass);color:#cfd6ff;border:1px solid var(--grid)}
.chip{font-size:12px;color:var(--muted);padding:4px 10px;border-radius:999px;background:var(--glass);border:1px solid var(--grid)}
.select{height:34px;padding:6px 10px;border-radius:10px}
</style>
</head>
<body>
<header class="header">
  <div class="title">
    <span class="icon" aria-hidden="true"></span>
    <div>
      <h1 data-i18n="title">Dashboard</h1>
      <div class="subtitle" data-i18n="subtitle">Arduino → Raspberry Pi · Live</div>
    </div>
  </div>
  <div style="display:flex; align-items:center; gap:8px">
    <label for="lang" class="subtitle" data-i18n="language">Language</label>
    <select id="lang" class="select" aria-label="Language">
      <option value="en">English</option>
      <option value="id">Bahasa Indonesia</option>
    </select>
    <div class="subtitle" id="stamp">—</div>
  </div>
</header>

<main class="container">
  <div id="alert" class="alert" style="display:none"></div>

  <div class="controls">
    <div class="control-card">
      <div class="small" data-i18n="temperature">Temperature</div>
      <div id="tempUnits" class="switch" style="margin-left:8px">
        <button class="btn" data-unit="F">°F</button>
        <button class="btn" data-unit="C">°C</button>
        <button class="btn" data-unit="K">K</button>
      </div>
    </div>

    <div class="control-card">
      <div class="small" data-i18n="humidity">Humidity</div>
      <div id="humUnits" class="switch" style="margin-left:8px">
        <button class="btn" data-unit="%">%</button>
        <button class="btn" data-unit="g/m³">g/m³</button>
      </div>
    </div>

    <!-- CLEANER CHART BAR -->
    <div class="control-card controls-bar">
      <span class="chip" data-i18n="chart">Chart</span>

      <label class="field">
        <span data-i18n="yTempStep">Y Temp step</span>
        <div style="display:flex;gap:8px;align-items:center">
          <select id="yStepTemp" class="select"></select>
          <span id="yTempUnitBadge" class="unit-badge">°F</span>
        </div>
      </label>

      <label class="field">
        <span data-i18n="yHumStep">Y Hum step</span>
        <select id="yStepHum" class="select">
          <option value="auto" data-i18n="auto" selected>Auto</option>
          <option value="0.1">0.1</option>
          <option value="0.25">0.25</option>
          <option value="0.5">0.5</option>
          <option value="1">1</option>
          <option value="2">2</option>
          <option value="5">5</option>
        </select>
      </label>

      <label class="field">
        <span data-i18n="xTicks">X ticks</span>
        <select id="xTicks" class="select">
          <option value="s:1"  data-i18n-opt="every1s">every 1 s</option>
          <option value="s:2"  data-i18n-opt="every2s">every 2 s</option>
          <option value="s:5"  data-i18n-opt="every5s">every 5 s</option>
          <option value="s:10" data-i18n-opt="every10s">every 10 s</option>
          <option value="s:15" data-i18n-opt="every15s">every 15 s</option>
          <option value="s:30" data-i18n-opt="every30s">every 30 s</option>
          <option value="m:1"  data-i18n-opt="every1m" selected>every 1 min</option>
          <option value="m:2"  data-i18n-opt="every2m">every 2 min</option>
          <option value="m:3"  data-i18n-opt="every3m">every 3 min</option>
          <option value="m:5"  data-i18n-opt="every5m">every 5 min</option>
          <option value="m:10" data-i18n-opt="every10m">every 10 min</option>
          <option value="m:15" data-i18n-opt="every15m">every 15 min</option>
          <option value="m:20" data-i18n-opt="every20m">every 20 min</option>
          <option value="m:30" data-i18n-opt="every30m">every 30 min</option>
        </select>
      </label>

      <label class="field">
        <span data-i18n="window">Window</span>
        <select id="timeWindow" class="select">
          <option value="1m"  data-i18n-opt="w1m">1 min</option>
          <option value="5m"  data-i18n-opt="w5m">5 min</option>
          <option value="15m" data-i18n-opt="w15m">15 min</option>
          <option value="30m" data-i18n-opt="w30m">30 min</option>
          <option value="1h"  data-i18n-opt="w1h" selected>1 hour</option>
          <option value="2h"  data-i18n-opt="w2h">2 hours</option>
          <option value="4h"  data-i18n-opt="w4h">4 hours</option>
          <option value="6h"  data-i18n-opt="w6h">6 hours</option>
          <option value="8h"  data-i18n-opt="w8h">8 hours</option>
          <option value="12h" data-i18n-opt="w12h">12 hours</option>
          <option value="24h" data-i18n-opt="w24h">24 hours</option>
          <option value="48h" data-i18n-opt="w48h">48 hours</option>
          <option value="72h" data-i18n-opt="w72h">72 hours</option>
        </select>
      </label>

      <label class="field">
        <span data-i18n="date">Date</span>
        <input id="datePick" type="date" class="select" style="height:34px" />
      </label>

      <button id="btnLive" class="btn" style="border:1px solid var(--grid)" data-i18n="live">Live</button>

      <div class="small kv" style="margin-left:auto">
        <span data-i18n="last">last</span> <span id="pointsCount">300</span> <span data-i18n="points">points</span>
      </div>
    </div>

    <div class="control-card" style="margin-left:0">
      <div class="small" data-i18n="update">Update</div>
      <div class="small"><span data-i18n="autoRefresh">Auto-refresh</span>
        <select id="refreshMs" class="select">
          <option value="0" data-i18n-opt="off">Off</option>
          <option value="500" data-i18n-opt="s05">0.5 s</option>
          <option value="1000" data-i18n-opt="s1" selected>1 s</option>
          <option value="2000" data-i18n-opt="s2">2 s</option>
          <option value="5000" data-i18n-opt="s5">5 s</option>
        </select>
      </div>
      <button id="refreshNow" class="btn" style="border:1px solid var(--grid)" data-i18n="refreshNow">Refresh now</button>
      <button id="reloadUi" class="btn" style="border:1px solid var(--grid)" title="Reload UI">Reload UI</button>
      <div class="small"><span data-i18n="port">Port</span> <span id="port" class="tag">—</span></div>
    </div>
  </div>

  <div class="card-grid">
    <div class="left-col">
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div><div class="small" data-i18n="avgLive">Averages · live</div><div id="avgLabel" style="font-size:14px;color:var(--muted)" data-i18n="tempHum">temperature / humidity</div></div>
          <div class="small"><span data-i18n="updated">Updated</span>: <span id="age" data-i18n-alt="noData">no data</span></div>
        </div>
        <div class="chart-box" style="margin-top:12px">
          <canvas id="chart"></canvas>
        </div>
        <div class="small" style="margin-top:8px" data-i18n="tip">Tip: switch units — the chart and all numbers convert instantly.</div>
      </div>

      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div><div class="small" data-i18n="extras">Extras</div><div style="font-size:14px;color:var(--muted)" data-i18n="dewAbs">Dew point & absolute humidity</div></div>
          <div class="small">—</div>
        </div>
        <div class="grid" style="margin-top:10px">
          <div class="card" style="padding:12px">
            <div class="small" data-i18n="dewAvg">Dew point (Avg)</div>
            <div id="dew" class="big">--.--</div>
            <div class="kv small" data-i18n="dewCalc">Calculated from avg temp & RH</div>
          </div>
          <div class="card" style="padding:12px">
            <div class="small" data-i18n="absHumAvg">Absolute Humidity (Avg)</div>
            <div id="absHum" class="big">--.--</div>
            <div class="kv small">g/m³</div>
          </div>
        </div>
      </div>
    </div>

    <div>
      <div class="card">
        <div style="display:flex;justify-content:space-between"><div class="small" data-i18n="currAvg">Current Averages</div><div class="small" data-i18n="status">Status</div></div>
        <div class="row"><div class="kv" data-i18n="temperature">Temperature</div><div id="avgF" class="big">--.--</div></div>
        <div class="row"><div class="kv" data-i18n="humidity">Humidity</div><div id="avgH" class="big">--.--</div></div>
        <div class="row"><div class="kv" data-i18n="age">Age</div><div id="ageTag" class="tag" data-i18n-alt="noData">no data</div></div>
      </div>

      <div class="card" style="margin-top:12px">
        <div class="small" data-i18n="sensors">Sensors</div>
        <div class="grid" style="margin-top:10px">
          <div class="card" style="padding:10px">
            <div class="small">DHT22</div>
            <div class="row"><div class="kv" data-i18n="temp">Temp</div><div id="t22" class="v">--.--</div></div>
            <div class="row"><div class="kv" data-i18n="hum">Humidity</div><div id="h22" class="v">--.--</div></div>
          </div>
          <div class="card" style="padding:10px">
            <div class="small">DHT11A</div>
            <div class="row"><div class="kv" data-i18n="temp">Temp</div><div id="ta" class="v">--.--</div></div>
            <div class="row"><div class="kv" data-i18n="hum">Humidity</div><div id="ha" class="v">--.--</div></div>
          </div>
          <div class="card" style="padding:10px">
            <div class="small">DHT11B</div>
            <div class="row"><div class="kv" data-i18n="temp">Temp</div><div id="tb" class="v">--.--</div></div>
            <div class="row"><div class="kv" data-i18n="hum">Humidity</div><div id="hb" class="v">--.--</div></div>
          </div>
        </div>
      </div>
    </div>

  </div>

  <div class="footer">
    <span data-i18n="designedFor">Designed for</span>
    <a href="https://epics.engineering.asu.edu/" target="_blank" rel="noopener">EPICS @ ASU</a> ·
    <a href="https://itb.ac.id/en/" target="_blank" rel="noopener">ITB</a> ·
    <span data-i18n="dataFrom">Data from Arduino JSON ·</span>
    <a href="#" id="downloadCsv" data-i18n="downloadCsv">Download CSV</a>
    <br/>
    <span data-i18n="credit1">Designed and created by</span>
    <a href="https://www.linkedin.com/in/piotrjandura" target="_blank" rel="noopener">Piotr Jandura</a>
    <span data-i18n="credit2">with the help of</span>
    <a href="https://www.linkedin.com/in/aarnav-kannan/" target="_blank" rel="noopener">Aarnav Kannan</a>,
    <a href="https://www.linkedin.com/in/haydennguyen14/" target="_blank" rel="noopener">Hayden Nguyen</a>
    <span data-i18n="credit3">for ideas and moral support.</span>
  </div>
</main>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0"></script>
<script>
/* -------- i18n -------- */
const I18N = {
  en: {
    title:"Dashboard", subtitle:"Arduino → Raspberry Pi · Live", language:"Language",
    temperature:"Temperature", humidity:"Humidity", chart:"Chart",
    yTempStep:"Y Temp step", yHumStep:"Y Hum step", xTicks:"X ticks", last:"last", points:"points",
    update:"Update", autoRefresh:"Auto-refresh", refreshNow:"Refresh now", port:"Port",
    avgLive:"Averages · live", tempHum:"temperature / humidity", updated:"Updated", tip:"Tip: switch units — the chart and all numbers convert instantly.",
    extras:"Extras", dewAbs:"Dew point & absolute humidity", dewAvg:"Dew point (Avg)", dewCalc:"Calculated from avg temp & RH",
    absHumAvg:"Absolute Humidity (Avg)", currAvg:"Current Averages", status:"Status", age:"Age", sensors:"Sensors",
    temp:"Temp", hum:"Humidity", noData:"no data",
    footerLeft:"Data from Arduino JSON ·", downloadCsv:"Download CSV",
    credit1:"Designed and created by", credit2:"with the help of", credit3:"for ideas and moral support.",
    auto:"Auto",
    every1s:"every 1 s", every2s:"every 2 s", every5s:"every 5 s", every10s:"every 10 s", every15s:"every 15 s", every30s:"every 30 s",
    every1m:"every 1 min", every2m:"every 2 min", every3m:"every 3 min", every5m:"every 5 min", every10m:"every 10 min", every15m:"every 15 min", every20m:"every 20 min", every30m:"every 30 min",
    off:"Off", s05:"0.5 s", s1:"1 s", s2:"2 s", s5:"5 s",
    avgTemp:"Avg Temp", avgHumLab:"Avg Humidity", tempAxis:"Temp", humAxis:"Humidity",
    errNet:"Network error", errNoDev:"No serial device found", errBadJson:"Bad JSON from serial", errSerial:"Serial error",
    date:"Date", live:"Live", window:"Window",
    w1m:"1 min", w5m:"5 min", w15m:"15 min", w30m:"30 min", w1h:"1 hour", w2h:"2 hours", w4h:"4 hours", w6h:"6 hours", w8h:"8 hours", w12h:"12 hours", w24h:"24 hours", w48h:"48 hours", w72h:"72 hours",
    designedFor:"Designed for", dataFrom:"Data from Arduino JSON ·"
  },
  id: {
    title:"Dasbor", subtitle:"Arduino → Raspberry Pi · Langsung", language:"Bahasa",
    temperature:"Suhu", humidity:"Kelembapan", chart:"Grafik",
    yTempStep:"Langkah sumbu Y Suhu", yHumStep:"Langkah sumbu Y Kelembapan", xTicks:"Tanda sumbu X", last:"terakhir", points:"titik",
    update:"Pembaruan", autoRefresh:"Penyegaran otomatis", refreshNow:"Segarkan sekarang", port:"Port",
    avgLive:"Rata-rata · langsung", tempHum:"suhu / kelembapan", updated:"Diperbarui", tip:"Tip: ganti satuan — grafik dan angka berubah seketika.",
    extras:"Ekstra", dewAbs:"Titik embun & kelembapan absolut", dewAvg:"Titik embun (Rata-rata)", dewCalc:"Dihitung dari suhu & RH rata-rata",
    absHumAvg:"Kelembapan Absolut (Rata-rata)", currAvg:"Rata-rata Saat Ini", status:"Status", age:"Usia", sensors:"Sensor",
    temp:"Suhu", hum:"Kelembapan", noData:"tidak ada data",
    footerLeft:"Penyegaran otomatis 1 dtk · Data dari Arduino JSON ·", downloadCsv:"Unduh CSV",
    credit1:"Dirancang dan dibuat oleh", credit2:"dengan bantuan", credit3:"untuk ide dan dukungan moral.",
    auto:"Otomatis",
    every1s:"setiap 1 dtk", every2s:"setiap 2 dtk", every5s:"setiap 5 dtk", every10s:"setiap 10 dtk", every15s:"setiap 15 dtk", every30s:"setiap 30 dtk",
    every1m:"setiap 1 mnt", every2m:"setiap 2 mnt", every3m:"setiap 3 mnt", every5m:"setiap 5 mnt", every10m:"setiap 10 mnt", every15m:"setiap 15 mnt", every20m:"setiap 20 mnt", every30m:"setiap 30 mnt",
    off:"Mati", s05:"0,5 dtk", s1:"1 dtk", s2:"2 dtk", s5:"5 dtk",
    avgTemp:"Rata-rata Suhu", avgHumLab:"Rata-rata Kelembapan", tempAxis:"Suhu", humAxis:"Kelembapan",
    errNet:"Galat jaringan", errNoDev:"Perangkat serial tidak ditemukan", errBadJson:"JSON serial buruk", errSerial:"Galat serial",
    date:"Tanggal", live:"Langsung", window:"Jendela",
    w1m:"1 mnt", w5m:"5 mnt", w15m:"15 mnt", w30m:"30 mnt", w1h:"1 jam", w2h:"2 jam", w4h:"4 jam", w6h:"6 jam", w8h:"8 jam", w12h:"12 jam", w24h:"24 jam", w48h:"48 jam", w72h:"72 jam",
    designedFor:"Dirancang untuk", dataFrom:"Data dari Arduino JSON ·"
  }
};
const LS_LANG = "ui.lang";
function getLang(){ const s=localStorage.getItem(LS_LANG); if(s&&I18N[s]) return s; return (navigator.language||"en").toLowerCase().startsWith("id")?"id":"en"; }
let CUR_LANG = getLang();
function t(key){ return (I18N[CUR_LANG] && I18N[CUR_LANG][key]) || key; }
function applyI18n(){
  document.documentElement.setAttribute("lang", CUR_LANG);
  document.querySelectorAll("[data-i18n]").forEach(el=>{ el.textContent = t(el.getAttribute("data-i18n")); });
  document.querySelectorAll("[data-i18n-opt]").forEach(opt=>{ opt.textContent = t(opt.getAttribute("data-i18n-opt")); });
  renderYTempSelect();
  if(window.myTempChart){
    window.myTempChart.data.datasets[0].label = t("avgTemp");
    window.myTempChart.data.datasets[1].label = t("avgHumLab");
    window.myTempChart.options.scales.y.title.text  = t("tempAxis");
    window.myTempChart.options.scales.y1.title.text = t("humAxis");
    window.myTempChart.update("none");
  }
}
document.addEventListener("DOMContentLoaded", ()=>{
  const sel=document.getElementById("lang");
  sel.value = CUR_LANG;
  sel.addEventListener("change", ()=>{ CUR_LANG=sel.value; localStorage.setItem(LS_LANG,CUR_LANG); applyI18n(); convertAllDisplays(); applyAxisSteps(); redrawFromRaw(); });
  applyI18n();
});

/* -------- existing app logic -------- */
let lastEpochMs = null;
let ageTimer = null;

function renderAgeTicker(){
  const ageEl=document.getElementById('age');
  const tagEl=document.getElementById('ageTag');
  const stampEl=document.getElementById('stamp');
  if(!lastEpochMs){
    ageEl.textContent=t('noData');
    tagEl.textContent=t('noData');
    return;
  }
  const secs=Math.max(0,Math.floor((Date.now()-lastEpochMs)/1000));
  const s=`${secs}s ago`;
  ageEl.textContent=s;
  tagEl.textContent=s;
  const base=stampEl.textContent.split('(')[0].trim();
  stampEl.textContent=`${base} (${s})`;
}
function startAgeTicker(){
  if(ageTimer) clearInterval(ageTimer);
  ageTimer = setInterval(renderAgeTicker, 1000);
  renderAgeTicker();
}

function fToC(f){ return (f - 32) * 5/9; }
function cToF(c){ return (c * 9/5) + 32; }
function fToK(f){ return fToC(f) + 273.15; }
function kToF(k){ return (k - 273.15) * 9/5 + 32; }
function dewPointC(tempC, rh){ if(tempC===null||rh===null) return null; const a=17.27,b=237.7; const alpha=(a*tempC)/(b+tempC)+Math.log(rh/100); return (b*alpha)/(a-alpha); }
function absHumidity(tempC, rh){ if(tempC===null||rh===null) return null; const es=6.112*Math.exp((17.67*tempC)/(tempC+243.5)); const e=es*rh/100; return 216.7*e/(tempC+273.15); }
function fmtNum(v,d=2,s=''){ return (v===null||v===undefined||Number.isNaN(v))?`--.-- ${s}`:`${Number(v).toFixed(d)} ${s}` }

const S=(k,v)=>v===undefined?localStorage.getItem(k):localStorage.setItem(k,v);

const DEFAULT_TEMP = S('tempUnit') || 'F';
const DEFAULT_HUM  = S('humUnit') || '%';
let tempUnit = DEFAULT_TEMP, humUnit = DEFAULT_HUM;

function yTempUnitSymbol(){ return tempUnit==='F'?'°F':tempUnit==='C'?'°C':'K'; }

function yTempOptions(){
  const vals = ['auto','0.1','0.25','0.5','1','2','5'];
  return vals.map(v => ({ val:v, label: v==='auto' ? t('auto') : v }));
}
function renderYTempSelect(){
  const sel = document.getElementById('yStepTemp');
  const cur = S('yStepTemp') || '1';
  const opts = yTempOptions().map(o => `<option value="${o.val}">${o.label}</option>`).join('');
  sel.innerHTML = opts;
  sel.value = cur;
  const badge = document.getElementById('yTempUnitBadge');
  if (badge) badge.textContent = yTempUnitSymbol();
}

function setActiveButtons(){
  document.querySelectorAll('#tempUnits .btn').forEach(b=>b.classList.toggle('active', b.dataset.unit===tempUnit));
  document.querySelectorAll('#humUnits .btn').forEach(b=>b.classList.toggle('active', b.dataset.unit===humUnit));
}
document.querySelectorAll('#tempUnits .btn').forEach(b=>{
  b.addEventListener('click', async ()=>{ tempUnit=b.dataset.unit; S('tempUnit',tempUnit); setActiveButtons(); renderYTempSelect(); applyAxisSteps(); convertAllDisplays(); await redrawFromRaw(); });
});
document.querySelectorAll('#humUnits .btn').forEach(b=>{
  b.addEventListener('click', async ()=>{ humUnit=b.dataset.unit; S('humUnit',humUnit); setActiveButtons(); applyAxisSteps(); convertAllDisplays(); await redrawFromRaw(); });
});
setActiveButtons();
renderYTempSelect();

let chart, rawTs=[], rawTempF=[], rawHumPct=[];
let refreshMs=parseInt(S('refreshMs')||'1000',10), refreshTimer=null;
let xTicksSpec=S('xTicks')||'m:1';
let yStepHum=S('yStepHum')||'auto';
let selectedDate = S('selectedDate') || '';
let timeWindow = S('timeWindow') || '1h';

// Smooth auto-scaling variables
let autoScaleDebounceTimer = null;
let lastAutoScaleTime = 0;
let smoothAxisUpdateTimer = null;
let currentYMin = null, currentYMax = null;
let currentY1Min = null, currentY1Max = null;


document.getElementById('xTicks').value=xTicksSpec;
document.getElementById('refreshMs').value=String(refreshMs);
document.getElementById('yStepHum').value=yStepHum;
const datePickEl = document.getElementById('datePick');
if(selectedDate){ datePickEl.value = selectedDate; }
const timeWindowEl = document.getElementById('timeWindow');
timeWindowEl.value = timeWindow;
document.getElementById('yStepTemp').addEventListener('change', async e=>{ S('yStepTemp', e.target.value); applyAxisSteps(); await redrawFromRaw(); });
document.getElementById('yStepHum').addEventListener('change', async e=>{ yStepHum=e.target.value; S('yStepHum',yStepHum); applyAxisSteps(); await redrawFromRaw(); });
document.getElementById('xTicks').addEventListener('change', async e=>{ xTicksSpec=e.target.value; S('xTicks',xTicksSpec); applyTimeTicks(); await redrawFromRaw(); });
document.getElementById('refreshMs').addEventListener('change', e=>{ refreshMs=parseInt(e.target.value,10)||0; S('refreshMs',String(refreshMs)); restartAutoRefresh(); });
document.getElementById('refreshNow').addEventListener('click', ()=>tick(true));
document.getElementById('reloadUi').addEventListener('click', ()=>{ location.reload(); });
function isTodaySelected(){
  if(!selectedDate) return false;
  const today = new Date();
  const y = today.getFullYear();
  const m = String(today.getMonth()+1).padStart(2,'0');
  const d = String(today.getDate()).padStart(2,'0');
  const todayStr = `${y}-${m}-${d}`;
  return selectedDate === todayStr;
}

function getTodayStr(){
  const today = new Date();
  const y = today.getFullYear();
  const m = String(today.getMonth()+1).padStart(2,'0');
  const d = String(today.getDate()).padStart(2,'0');
  return `${y}-${m}-${d}`;
}

function getWindowSeconds(){
  if(typeof timeWindow !== 'string') return 3600;
  if(timeWindow.endsWith('m')) return Math.max(1, parseInt(timeWindow,10)) * 60;
  if(timeWindow.endsWith('h')) return Math.max(1, parseInt(timeWindow,10)) * 3600;
  if(timeWindow.endsWith('d')) return Math.max(1, parseInt(timeWindow,10)) * 86400; // days
  return 3600;
}

function getTickIntervalSeconds(){
  const [unit, value] = xTicksSpec.split(':');
  const num = parseInt(value, 10);
  if(unit === 's') return num;
  if(unit === 'm') return num * 60;
  return 10; // default 10 seconds
}

function filterDataByInterval(points){
  if(!points || points.length === 0) return points;
  
  const intervalSeconds = getTickIntervalSeconds() * 1000; // convert to milliseconds
  const filtered = [];
  let lastIntervalTime = null;
  
  for(const point of points){
    if(!point || point.x === null || point.y === null) continue;
    
    const intervalTime = Math.floor(point.x / intervalSeconds) * intervalSeconds;
    
    // Only include points that fall on the interval boundaries
    if(lastIntervalTime === null || intervalTime > lastIntervalTime){
      filtered.push(point);
      lastIntervalTime = intervalTime;
    }
  }
  
  return filtered;
}

function liveMaxPoints(){
  const seconds = getWindowSeconds();
  return Math.min(20000, Math.max(300, seconds));
}

async function backfillTodayIfLive(){
  if(!selectedDate || isTodaySelected()){
    try{
      await loadHistoryForDate(getTodayStr());
    }catch(e){ console.warn('backfillTodayIfLive failed', e); }
  }
}

datePickEl.addEventListener('change', async (e)=>{
  const v=e.target.value;
  selectedDate = v || '';
  S('selectedDate', selectedDate);
  if(selectedDate){
    await loadHistoryForDate(selectedDate);
    if(!isTodaySelected()){
      if(refreshTimer){ clearInterval(refreshTimer); refreshTimer=null; }
    } else {
      restartAutoRefresh();
    }
  } else {
    await initChart();
    restartAutoRefresh();
  }
});
document.getElementById('btnLive').addEventListener('click', async ()=>{
  selectedDate=''; S('selectedDate','');
  if(datePickEl) datePickEl.value='';
  
  // Reset all controls to default values
  resetControlsToDefaults();
  
  // Clear existing data and reload
  rawTs = [];
  rawTempF = [];
  rawHumPct = [];
  
  // Reinitialize chart and load fresh data
  await initChart(); 
  await backfillTodayIfLive();
  restartAutoRefresh();
});

function resetControlsToDefaults(){
  // Reset X ticks to default
  xTicksSpec = 'm:1';
  S('xTicks', xTicksSpec);
  document.getElementById('xTicks').value = xTicksSpec;
  
  // Reset Y temp step to default
  S('yStepTemp', 'auto');
  document.getElementById('yStepTemp').value = 'auto';
  
  // Reset Y hum step to default
  yStepHum = 'auto';
  S('yStepHum', yStepHum);
  document.getElementById('yStepHum').value = yStepHum;
  
  // Reset time window to default
  timeWindow = '1h';
  S('timeWindow', timeWindow);
  document.getElementById('timeWindow').value = timeWindow;
  
  // Reset auto-scaling state
  currentYMin = null;
  currentYMax = null;
  currentY1Min = null;
  currentY1Max = null;
}

let timeWindowDebounceTimer = null;
timeWindowEl.addEventListener('change', async ()=>{
  timeWindow=timeWindowEl.value; S('timeWindow', timeWindow);
  
  // Clear existing debounce timer
  if(timeWindowDebounceTimer) {
    clearTimeout(timeWindowDebounceTimer);
  }
  
  // Debounce the expensive operations
  timeWindowDebounceTimer = setTimeout(async () => {
    await backfillTodayIfLive();
    await redrawFromRaw();
  }, 300); // 300ms debounce
});

function ticksCfg(){
  const [u,s]=xTicksSpec.split(':'); const step=parseInt(s,10);
  return { unit:(u==='s'?'second':'minute'), stepSize:step };
}

async function initChart(){
  // Clean up any existing smooth scaling timers
  cleanupSmoothScaling();
  
  const ctx = document.getElementById('chart').getContext('2d');
  const res = await fetch('/history',{cache:'no-store'});
  const h = await res.json();
  rawTs     = (h.ts||[]).map(t=>t*1000);
  rawTempF  = h.avgF||[];
  rawHumPct = h.avgH||[];
  document.getElementById('pointsCount').textContent = String(rawTs.length);

  chart = new Chart(ctx,{
    type:'line',
    data:{datasets:[
      {
        label:t('avgTemp'),    
        data:[], 
        parsing:false, 
        yAxisID:'y',  
        tension:0.1,           // Low tension for more accurate representation
        pointRadius:2,         // Show points for interval-based display
        borderWidth:2,
        stepped:false,
        spanGaps:false         // Don't connect across gaps for interval separation
      },
      {
        label:t('avgHumLab'),  
        data:[], 
        parsing:false, 
        yAxisID:'y1', 
        tension:0.1,           // Low tension for more accurate representation
        pointRadius:2,         // Show points for interval-based display
        borderWidth:2,
        stepped:false,
        spanGaps:false         // Don't connect across gaps for interval separation
      }
    ]},
    options:{
      responsive:true, 
      maintainAspectRatio:false, 
      animation:false,
      interaction:{mode:'index', intersect:false},
      scales:{
        x:{type:'time', time:{...ticksCfg()}, ticks:{color:'#cfd6ff'}, grid:{color:'rgba(255,255,255,0.03)',display:false}},
        y:{position:'left', title:{display:true,text:t('tempAxis')}, ticks:{color:'#cfd6ff'}},
        y1:{position:'right', grid:{display:false}, title:{display:true,text:t('humAxis')}, ticks:{color:'#cfd6ff'}}
      },
      plugins:{legend:{labels:{color:'#cfd6ff'}}}
    }
  });
  window.myTempChart = chart;
  applyAxisSteps();
  await redrawFromRaw();
}

async function loadHistoryForDate(dayStr){
  try{
    const res = await fetch(`/history?date=${encodeURIComponent(dayStr)}`, {cache:'no-store'});
    const h = await res.json();
    rawTs     = (h.ts||[]).map(t=>t*1000);
    rawTempF  = h.avgF||[];
    rawHumPct = h.avgH||[];
    document.getElementById('pointsCount').textContent = String(rawTs.length);
    await redrawFromRaw();
  }catch(e){ console.error('loadHistoryForDate error', e); }
}

function applyTimeTicks(){
  if(!chart) return;
  chart.options.scales.x.time = {...ticksCfg()};
  chart.update();
}

function calculateSmoothAxisRange(dataPoints, currentMin, currentMax, minStepSize = 0.1) {
  if (!dataPoints || dataPoints.length === 0) return { min: currentMin, max: currentMax };
  
  const validPoints = dataPoints.filter(p => p !== null && p.y !== null && p.y !== undefined);
  if (validPoints.length === 0) return { min: currentMin, max: currentMax };
  
  const values = validPoints.map(p => p.y);
  const dataMin = Math.min(...values);
  const dataMax = Math.max(...values);
  
  // Add padding (10% of range)
  const range = dataMax - dataMin;
  const padding = Math.max(range * 0.1, minStepSize);
  let newMin = dataMin - padding;
  let newMax = dataMax + padding;
  
  // Ensure minimum step size
  const stepRange = newMax - newMin;
  if (stepRange < minStepSize) {
    const center = (newMin + newMax) / 2;
    newMin = center - minStepSize / 2;
    newMax = center + minStepSize / 2;
  }
  
  // Smooth transition from current values if they exist
  if (currentMin !== null && currentMax !== null) {
    const transitionFactor = 0.3; // How quickly to adapt (0.1 = very slow, 0.5 = fast)
    newMin = currentMin + (newMin - currentMin) * transitionFactor;
    newMax = currentMax + (newMax - currentMax) * transitionFactor;
  }
  
  return { min: newMin, max: newMax };
}

function debouncedAutoScale() {
  if (autoScaleDebounceTimer) {
    clearTimeout(autoScaleDebounceTimer);
  }
  
  autoScaleDebounceTimer = setTimeout(() => {
    const now = Date.now();
    // Only update auto-scaling every 2 seconds to reduce jitter
    if (now - lastAutoScaleTime < 2000) return;
    lastAutoScaleTime = now;
    
    smoothAutoScale();
  }, 500); // 500ms debounce
}

function smoothAutoScale() {
  if (!chart) return;
  
  const yStepTemp = S('yStepTemp') || '1';
  const yStepHum = S('yStepHum') || '1';
  
  // Only apply smooth auto-scaling if auto is selected
  if (yStepTemp === 'auto') {
    const tempPts = chart.data.datasets[0].data || [];
    const tempRange = calculateSmoothAxisRange(tempPts, currentYMin, currentYMax, 0.1);
    currentYMin = tempRange.min;
    currentYMax = tempRange.max;
    
    chart.options.scales.y.min = currentYMin;
    chart.options.scales.y.max = currentYMax;
    chart.options.scales.y.ticks.stepSize = undefined;
  } else {
    chart.options.scales.y.min = undefined;
    chart.options.scales.y.max = undefined;
    chart.options.scales.y.ticks.stepSize = parseFloat(yStepTemp);
  }
  
  if (yStepHum === 'auto') {
    const humPts = chart.data.datasets[1].data || [];
    const humRange = calculateSmoothAxisRange(humPts, currentY1Min, currentY1Max, 0.1);
    currentY1Min = humRange.min;
    currentY1Max = humRange.max;
    
    chart.options.scales.y1.min = currentY1Min;
    chart.options.scales.y1.max = currentY1Max;
    chart.options.scales.y1.ticks.stepSize = undefined;
  } else {
    chart.options.scales.y1.min = undefined;
    chart.options.scales.y1.max = undefined;
    chart.options.scales.y1.ticks.stepSize = parseFloat(yStepHum);
  }
  
  // Update axis titles
  chart.options.scales.y.title.text = (tempUnit === 'F') ? t('tempAxis') + ' (°F)' : (tempUnit === 'C') ? t('tempAxis') + ' (°C)' : t('tempAxis') + ' (K)';
  chart.options.scales.y1.title.text = (humUnit === '%') ? t('humAxis') + ' (%)' : t('humAxis') + ' (g/m³)';
  
  chart.update('none');
}

function applyAxisSteps(){
  if(!chart) return;
  const yStepTemp = S('yStepTemp')||'1';
  const yStepHum = S('yStepHum')||'1';
  
  // Reset smooth scaling state when manually changing steps
  if (yStepTemp !== 'auto') {
    currentYMin = null;
    currentYMax = null;
  }
  if (yStepHum !== 'auto') {
    currentY1Min = null;
    currentY1Max = null;
  }
  
  chart.options.scales.y.ticks.stepSize  = (yStepTemp==='auto')?undefined:parseFloat(yStepTemp);
  chart.options.scales.y1.ticks.stepSize = (yStepHum==='auto')?undefined:parseFloat(yStepHum);
  chart.options.scales.y.title.text  = (tempUnit==='F')?t('tempAxis')+' (°F)':(tempUnit==='C')?t('tempAxis')+' (°C)':t('tempAxis')+' (K)';
  chart.options.scales.y1.title.text = (humUnit==='%')?t('humAxis')+' (%)':t('humAxis')+' (g/m³)';
  
  // If auto is selected, trigger smooth scaling
  if (yStepTemp === 'auto' || yStepHum === 'auto') {
    debouncedAutoScale();
  } else {
    chart.update('none');
  }
}

async function toDisplayPoints(){
  let minTs = -Infinity;
  let allTs = [...rawTs];
  let allTempF = [...rawTempF];
  let allHumPct = [...rawHumPct];
  
  if(rawTs.length>0){
    const lastTs = rawTs[rawTs.length-1];
    const windowSeconds = getWindowSeconds();
    
    // Debug logging
    console.log('Window:', timeWindow, 'Seconds:', windowSeconds, 'Selected Date:', selectedDate);
    
    // Handle different window types
    if(windowSeconds === 86400) { // Exactly 24 hours
      if(selectedDate) {
        // For selected dates, show full 24 hours of that day
        const selectedDateObj = new Date(selectedDate + 'T00:00:00');
        minTs = selectedDateObj.getTime();
      } else {
        // For live mode, show from start of current day to now
        const today = new Date();
        const startOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate());
        minTs = startOfDay.getTime();
      }
    } else if(windowSeconds > 86400) {
      // For 48h, 72h, etc. - use rolling window from current time
      const currentTime = Date.now();
      minTs = currentTime - windowSeconds*1000;
    } else {
      // For shorter windows (1h, 2h, etc.) - use rolling window from current time
      const currentTime = Date.now();
      minTs = currentTime - windowSeconds*1000;
    }
    
    // For multi-day windows, load additional historical data
    if(windowSeconds > 86400) { // More than 24 hours (48h, 72h, etc.)
      const daysNeeded = Math.ceil(windowSeconds / 86400);
      const today = new Date();
      
      for(let i = 1; i < daysNeeded; i++) {
        const pastDate = new Date(today);
        pastDate.setDate(today.getDate() - i);
        const dateStr = pastDate.toISOString().split('T')[0];
        
        try {
          const response = await fetch(`/history?date=${dateStr}`, {cache:'no-store'});
          const historyData = await response.json();
          
          if(historyData.ts && historyData.ts.length > 0) {
            const pastTs = historyData.ts.map(t => t * 1000);
            const pastTempF = historyData.avgF || [];
            const pastHumPct = historyData.avgH || [];
            
            // Combine with existing data, keeping chronological order
            const combinedTs = [...pastTs, ...allTs];
            const combinedTempF = [...pastTempF, ...allTempF];
            const combinedHumPct = [...pastHumPct, ...allHumPct];
            
            // Sort by timestamp
            const sortedIndices = combinedTs.map((_, i) => i).sort((a, b) => combinedTs[a] - combinedTs[b]);
            
            allTs = sortedIndices.map(i => combinedTs[i]);
            allTempF = sortedIndices.map(i => combinedTempF[i]);
            allHumPct = sortedIndices.map(i => combinedHumPct[i]);
          }
        } catch (error) {
          console.warn(`Failed to load data for ${dateStr}:`, error);
        }
      }
    } else if(windowSeconds === 86400 && selectedDate) {
      // For 24-hour selected dates, we already have the data loaded
      // No additional data loading needed
    }
  }
  
  const tempPts = allTs.map((t,i)=>{
    const f = allTempF[i]; if(f==null) return null;
    let y=f;
    if(tempUnit==='C') y=fToC(f);
    else if(tempUnit==='K') y=fToC(f)+273.15;
    return {x:t,y};
  }).filter(p=>p && p.x>=minTs);

  const humPts = allTs.map((t,i)=>{
    const h = allHumPct[i]; if(h==null) return null;
    if(humUnit==='%') return {x:t,y:h};
    const f = allTempF[i]; if(f==null) return null;
    return {x:t,y:absHumidity(fToC(f),h)};
  }).filter(p=>p && p.x>=minTs);

  return {tempPts, humPts};
}

async function redrawFromRaw(){
  if(!chart) return;
  try {
    // Show loading indicator
    const loadingEl = document.getElementById('pointsCount');
    const originalText = loadingEl.textContent;
    loadingEl.textContent = 'Loading...';
    
    // Load data asynchronously
    const {tempPts, humPts} = await toDisplayPoints();
    
    // Filter data points by X tick interval for visual separation
    const filteredTempPts = filterDataByInterval(tempPts);
    const filteredHumPts = filterDataByInterval(humPts);
    
    chart.data.datasets[0].data=filteredTempPts;
    chart.data.datasets[1].data=filteredHumPts;
  if(tempPts.length>0 || humPts.length>0){
    const all = [...tempPts, ...humPts];
    const minX = Math.min(...all.map(p=>p.x));
    const maxX = Math.max(...all.map(p=>p.x));
    
    // Set proper X-axis range based on window type
    const windowSeconds = getWindowSeconds();
    if(windowSeconds === 86400) { // Exactly 24 hours
      if(selectedDate) {
        // For selected dates, show full 24 hours
        const selectedDateObj = new Date(selectedDate + 'T00:00:00');
        const endOfDay = new Date(selectedDateObj);
        endOfDay.setDate(endOfDay.getDate() + 1);
        chart.options.scales.x.min = selectedDateObj.getTime();
        chart.options.scales.x.max = endOfDay.getTime();
      } else {
        // For live mode, show from start of day to now
        const today = new Date();
        const startOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate());
        chart.options.scales.x.min = startOfDay.getTime();
        chart.options.scales.x.max = Date.now();
      }
    } else {
      // For all other windows (1h, 2h, 48h, 72h, etc.), use data range
      chart.options.scales.x.min = minX;
      chart.options.scales.x.max = maxX;
    }
  } else {
    chart.options.scales.x.min = undefined;
    chart.options.scales.x.max = undefined;
  }
  document.getElementById('pointsCount').textContent = String(Math.max(chart.data.datasets[0].data.length, chart.data.datasets[1].data.length));
  
    // Trigger smooth auto-scaling if auto is enabled
    const yStepTemp = S('yStepTemp') || '1';
    const yStepHum = S('yStepHum') || '1';
    if (yStepTemp === 'auto' || yStepHum === 'auto') {
      debouncedAutoScale();
    } else {
      chart.update();
    }
  } catch (error) {
    console.error('Error in redrawFromRaw:', error);
    // Fallback to basic chart update
    if (chart) chart.update();
  }
}

function convertAllDisplays(){
  const _ = document.getElementById('age').textContent;
}

function localizeError(msg){
  if(!msg) return '';
  if(msg.startsWith('No serial device')) return t('errNoDev');
  if(msg.startsWith('Bad JSON')) return t('errBadJson');
  if(msg.startsWith('Serial error')) return t('errSerial')+': '+msg.split(':').slice(1).join(':').trim();
  return msg;
}

function updatePanels(d){
  const avgF = d?.averages?.temp_f ?? null;
  const avgH = d?.averages?.humidity ?? null;

  let avgTempDisp = avgF;
  if(tempUnit==='C') avgTempDisp = fToC(avgF);
  else if(tempUnit==='K') avgTempDisp = fToC(avgF)+273.15;

  let avgHumDisp = avgH;
  if(humUnit!=='%') avgHumDisp = (avgF==null||avgH==null)?null:absHumidity(fToC(avgF), avgH);

  document.getElementById('avgF').textContent = fmtNum(avgTempDisp,2, tempUnit==='F'?'°F':tempUnit==='C'?'°C':'K');
  document.getElementById('avgH').textContent = fmtNum(avgHumDisp,2, humUnit==='%'?'%':'g/m³');

  let dew=null, absH=null;
  if(avgF!=null && avgH!=null){
    const tC=fToC(avgF);
    const a=17.27,b=237.7; const alpha=(a*tC)/(b+tC)+Math.log(avgH/100); const dpC=(b*alpha)/(a-alpha);
    dew = tempUnit==='F'?cToF(dpC):tempUnit==='C'?dpC:dpC+273.15;
    absH = absHumidity(tC,avgH);
  }
  document.getElementById('dew').textContent   = fmtNum(dew,2, tempUnit==='F'?'°F':tempUnit==='C'?'°C':'K');
  document.getElementById('absHum').textContent= fmtNum(absH,2,'g/m³');

  if(d?.sensors){
    const ss=d.sensors, temps=[ss[0].temp_f,ss[1].temp_f,ss[2].temp_f], hums=[ss[0].humidity,ss[1].humidity,ss[2].humidity];
    ['t22','ta','tb'].forEach((id,i)=>{
      const v=temps[i]; const disp = v==null?null:(tempUnit==='F'?v:tempUnit==='C'?fToC(v):fToC(v)+273.15);
      document.getElementById(id).textContent = fmtNum(disp,2, tempUnit==='F'?'°F':tempUnit==='C'?'°C':'K');
    });
    ['h22','ha','hb'].forEach((id,i)=>{
      const h=hums[i];
      const disp = humUnit==='%'?h: (h==null||temps[i]==null)?null:absHumidity(fToC(temps[i]), h);
      document.getElementById(id).textContent = fmtNum(disp,2, humUnit==='%'?'%':'g/m³');
    });
  }
}

async function tick(forceAppend=false){
  try{
    const r=await fetch('/data',{cache:'no-store'}); const d=await r.json();
    const s=d.status||{};
    document.getElementById('port').textContent = s.port||'none';
    const alert=document.getElementById('alert');
    if(s.error){ alert.textContent=localizeError(s.error); alert.style.display='block'; } else { alert.style.display='none'; }

    if (d.updated.local) {
      document.getElementById('stamp').textContent = d.updated.local;
      lastEpochMs = Math.floor(d.updated.epoch * 1000);
      startAgeTicker();
    } else {
      document.getElementById('stamp').textContent = '—';
    }

    updatePanels(d);

    if(d.updated.epoch){
      lastEpochMs = Math.floor(d.updated.epoch*1000);
      startAgeTicker();

      if(!selectedDate || isTodaySelected()){
        const tms = lastEpochMs;
        const last = rawTs[rawTs.length-1];
        if(forceAppend || last==null || tms!==last){
          rawTs.push(tms);
          rawTempF.push(d.averages.temp_f ?? null);
          rawHumPct.push(d.averages.humidity ?? null);
          const max=liveMaxPoints();
          if(rawTs.length>max){
            const rm=rawTs.length-max;
            rawTs.splice(0,rm); rawTempF.splice(0,rm); rawHumPct.splice(0,rm);
          }
          document.getElementById('pointsCount').textContent = String(rawTs.length);
          await redrawFromRaw();
          
          // Trigger smooth auto-scaling for live updates
          const yStepTemp = S('yStepTemp') || '1';
          const yStepHum = S('yStepHum') || '1';
          if (yStepTemp === 'auto' || yStepHum === 'auto') {
            debouncedAutoScale();
          }
        }
      }
    }
  }catch(err){
    console.error('tick error', err);
    const a=document.getElementById('alert'); a.textContent=t('errNet'); a.style.display='block';
  }
}

function restartAutoRefresh(){
  if(refreshTimer){ clearInterval(refreshTimer); refreshTimer=null; }
  if(refreshMs>0){ refreshTimer=setInterval(()=>tick(false), refreshMs); }
}

function cleanupSmoothScaling(){
  if(autoScaleDebounceTimer){ clearTimeout(autoScaleDebounceTimer); autoScaleDebounceTimer=null; }
  if(smoothAxisUpdateTimer){ clearTimeout(smoothAxisUpdateTimer); smoothAxisUpdateTimer=null; }
}


document.getElementById('downloadCsv').addEventListener('click', async (e)=>{
  e.preventDefault();
  const urlSel = selectedDate ? `/history?date=${encodeURIComponent(selectedDate)}` : '/history';
  const res=await fetch(urlSel,{cache:'no-store'}); const h=await res.json();
  const rows=[["time","epoch_s","avgF(°F)","avgH(%)"]];
  (h.labels||[]).forEach((t,i)=>rows.push([t, h.ts?.[i]??'', h.avgF?.[i]??'', h.avgH?.[i]??'']))
  const csv=rows.map(r=>r.join(',')).join('\\n');
  const blob=new Blob([csv],{type:'text/csv'}); const url=URL.createObjectURL(blob);
  const a=document.createElement('a'); a.href=url; a.download='env-history.csv'; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
});

(async ()=>{ await initChart(); if(selectedDate){ await loadHistoryForDate(selectedDate); } else { await backfillTodayIfLive(); } await tick(true); if(!selectedDate || (typeof isTodaySelected==='function' && isTodaySelected())){ restartAutoRefresh(); } applyTimeTicks(); startAgeTicker(); })();
</script>
</body>
</html>
"""
@app.route("/")
def index(): return render_template_string(TEMPLATE)

if __name__ == "__main__":
    load_hist_from_csv()
    threading.Thread(target=reader, daemon=True).start()
    app.run(host="0.0.0.0", port=8000)

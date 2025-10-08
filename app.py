# app.py
import json, threading, time, math, glob
from datetime import datetime
from flask import Flask, jsonify, render_template_string, send_from_directory
import serial

BAUD = 115200
SERIAL_SCAN = ["/dev/ttyACM*", "/dev/ttyUSB*"]

latest_raw, latest_ts = None, None
last_err, cur_port = None, None

HIST_MAX = 300
hist = {"labels": [], "avgF": [], "avgH": []}  # labels are HH:MM:SS strings

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
                    hist["labels"].append(datetime.now().strftime("%H:%M:%S"))
                    hist["avgF"].append(float(af))
                    hist["avgH"].append(float(ah) if isinstance(ah,(int,float)) and not math.isnan(ah) else None)
                    for k in hist: hist[k] = hist[k][-HIST_MAX:]
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
def history(): return jsonify(hist)

TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard</title>

<!-- Favicon (inline P) -->
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
.icon{
  width:22px;height:22px;display:inline-block;border-radius:50%;
  background:radial-gradient(circle at 30% 30%, #a8dcff, #7cc8ff 60%, #4aa6e6);
  position:relative; box-shadow:0 0 0 1px rgba(36,49,71,.6) inset;
}
.icon:after{
  content:""; position:absolute; left:8px; top:4px; width:6px; height:12px; border-radius:3px;
  background:#0b1220;
}
h1{font-size:18px;margin:0}.subtitle{color:var(--muted);font-size:13px}
.container{max-width:1100px;margin:22px auto;padding:0 18px}
.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
.control-card{background:var(--card);border-radius:12px;padding:10px 12px;border:1px solid var(--grid);display:flex;gap:8px;align-items:center}
.switch{display:inline-flex;gap:8px;align-items:center}
.btn{background:transparent;border:1px solid transparent;padding:6px 10px;border-radius:8px;cursor:pointer;color:var(--muted)}
.btn.active{border-color:var(--accent);color:var(--accent);background:linear-gradient(180deg,rgba(124,200,255,0.04),transparent)}
.card-grid{display:grid;grid-template-columns:1.2fr 1fr;gap:16px}
.left-col{display:flex;flex-direction:column;gap:16px}
.card{background:var(--card);border-radius:12px;padding:14px;border:1px solid var(--grid);box-shadow:0 6px 20px rgba(0,0,0,.25)}
.small{font-size:13px;color:var(--muted)}
.row{display:flex;justify-content:space-between;align-items:center;margin-top:6px}
.big{font-size:34px;font-variant-numeric:tabular-nums}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:8px}
.tag{font-size:12px;padding:6px 10px;border-radius:999px;background:var(--glass);color:var(--muted)}
.alert{background:#3a1f1f;border:1px solid #5a2d2d;color:#ffd6d6;border-radius:10px;padding:10px;margin:12px 0}
.footer{text-align:center;color:var(--muted);font-size:12px;padding:18px;margin-top:8px}
.kv{color:var(--muted);font-size:13px}
.footer a{color:var(--accent);text-decoration:none}
@media (max-width:900px){
 .card-grid{grid-template-columns:1fr}
 .big{font-size:28px}
}
/* chart box constraints */
.chart-box{
  position: relative;
  height: 260px;
  max-height: 36vh;
  min-height: 180px;
  width: 100%;
}
#chart{
  position: absolute;
  inset: 0;
  width: 100% !important;
  height: 100% !important;
  border-radius:10px;
  background:linear-gradient(180deg,rgba(255,255,255,.01),transparent);
}
</style>
</head>
<body>
<header class="header">
  <div class="title">
    <span class="icon" aria-hidden="true"></span>
    <div>
      <h1>Dashboard</h1>
      <div class="subtitle">Arduino → Raspberry Pi · Live</div>
    </div>
  </div>
  <div class="subtitle" id="stamp">—</div>
</header>

<main class="container">
  <div id="alert" class="alert" style="display:none"></div>

  <div class="controls">
    <div class="control-card">
      <div class="small">Temperature</div>
      <div id="tempUnits" class="switch" style="margin-left:8px">
        <button class="btn" data-unit="F">°F</button>
        <button class="btn" data-unit="C">°C</button>
        <button class="btn" data-unit="K">K</button>
      </div>
    </div>

    <div class="control-card">
      <div class="small">Humidity</div>
      <div id="humUnits" class="switch" style="margin-left:8px">
        <button class="btn" data-unit="%">%</button>
        <button class="btn" data-unit="g/m³">g/m³</button>
      </div>
    </div>

    <div class="control-card">
      <div class="small">Chart</div>
      <div style="margin-left:8px" class="small kv">last <span id="pointsCount">300</span> points</div>
    </div>

    <div class="control-card" style="margin-left:auto">
      <div class="small">Port</div>
      <div id="port" class="tag" style="margin-left:8px">—</div>
    </div>
  </div>

  <div class="card-grid">
    <div class="left-col">
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div><div class="small">Averages · live</div><div id="avgLabel" style="font-size:14px;color:var(--muted)">temperature / humidity</div></div>
          <div class="small">Updated: <span id="age">no data</span></div>
        </div>
        <div class="chart-box" style="margin-top:12px">
          <canvas id="chart"></canvas>
        </div>
        <div class="small" style="margin-top:8px">Tip: switch units — the chart and all numbers convert instantly.</div>
      </div>

      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div><div class="small">Extras</div><div style="font-size:14px;color:var(--muted)">Dew point & absolute humidity</div></div>
          <div class="small">—</div>
        </div>
        <div class="grid" style="margin-top:10px">
          <div class="card" style="padding:12px">
            <div class="small">Dew point (Avg)</div>
            <div id="dew" class="big">--.--</div>
            <div class="kv small">Calculated from avg temp & RH</div>
          </div>
          <div class="card" style="padding:12px">
            <div class="small">Absolute Humidity (Avg)</div>
            <div id="absHum" class="big">--.--</div>
            <div class="kv small">g/m³</div>
          </div>
        </div>
      </div>
    </div>

    <div>
      <div class="card">
        <div style="display:flex;justify-content:space-between"><div class="small">Current Averages</div><div class="small">Status</div></div>
        <div class="row"><div class="kv">Temperature</div><div id="avgF" class="big">--.--</div></div>
        <div class="row"><div class="kv">Humidity</div><div id="avgH" class="big">--.--</div></div>
        <div class="row"><div class="kv">Age</div><div id="ageTag" class="tag">no data</div></div>
      </div>

      <div class="card" style="margin-top:12px">
        <div class="small">Sensors</div>
        <div class="grid" style="margin-top:10px">
          <div class="card" style="padding:10px">
            <div class="small">DHT22</div>
            <div class="row"><div class="kv">Temp</div><div id="t22" class="v">--.--</div></div>
            <div class="row"><div class="kv">Humidity</div><div id="h22" class="v">--.--</div></div>
          </div>
          <div class="card" style="padding:10px">
            <div class="small">DHT11A</div>
            <div class="row"><div class="kv">Temp</div><div id="ta" class="v">--.--</div></div>
            <div class="row"><div class="kv">Humidity</div><div id="ha" class="v">--.--</div></div>
          </div>
          <div class="card" style="padding:10px">
            <div class="small">DHT11B</div>
            <div class="row"><div class="kv">Temp</div><div id="tb" class="v">--.--</div></div>
            <div class="row"><div class="kv">Humidity</div><div id="hb" class="v">--.--</div></div>
          </div>
        </div>
      </div>
    </div>

  </div>

  <div class="footer">
    Auto-refresh 1s · Data from Arduino JSON ·
    <a href="#" id="downloadCsv">Download CSV</a>
    <br/>
    Designed and created by <a href="https://www.linkedin.com/in/piotrjandura" target="_blank" rel="noopener">Piotr Jandura</a>
    with the help of <a href="https://www.linkedin.com/in/aarnav-kannan/" target="_blank" rel="noopener">Aarnav Kannan</a> for ideas and moral support.
  </div>
</main>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>
function fToC(f){ return (f - 32) * 5/9; }
function cToF(c){ return (c * 9/5) + 32; }
function fToK(f){ return fToC(f) + 273.15; }
function kToF(k){ return (k - 273.15) * 9/5 + 32; }
function dewPointC(tempC, rh){ if(tempC===null||rh===null) return null; const a=17.27,b=237.7; const alpha=(a*tempC)/(b+tempC)+Math.log(rh/100); return (b*alpha)/(a-alpha); }
function absHumidity(tempC, rh){ if(tempC===null||rh===null) return null; const es=6.112*Math.exp((17.67*tempC)/(tempC+243.5)); const e=es*rh/100; return 216.7*e/(tempC+273.15); }
function fmtNum(v,d=2,s=''){ return (v===null||v===undefined||Number.isNaN(v))?`--.-- ${s}`:`${Number(v).toFixed(d)} ${s}` }

const DEFAULT_TEMP = localStorage.getItem('tempUnit') || 'F';
const DEFAULT_HUM = localStorage.getItem('humUnit') || '%';
let tempUnit = DEFAULT_TEMP, humUnit = DEFAULT_HUM;

function setActiveButtons(){
  document.querySelectorAll('#tempUnits .btn').forEach(b=>b.classList.toggle('active', b.dataset.unit===tempUnit));
  document.querySelectorAll('#humUnits .btn').forEach(b=>b.classList.toggle('active', b.dataset.unit===humUnit));
}
document.querySelectorAll('#tempUnits .btn').forEach(b=>{
  b.addEventListener('click', ()=>{ tempUnit=b.dataset.unit; localStorage.setItem('tempUnit',tempUnit); setActiveButtons(); convertAllDisplays(); convertChartDatasets(); });
});
document.querySelectorAll('#humUnits .btn').forEach(b=>{
  b.addEventListener('click', ()=>{ humUnit=b.dataset.unit; localStorage.setItem('humUnit',humUnit); setActiveButtons(); convertAllDisplays(); convertChartDatasets(); });
});
setActiveButtons();

let chart;
async function initChart(){
  const ctx = document.getElementById('chart').getContext('2d');
  const res = await fetch('/history',{cache:'no-store'});
  const hist = await res.json();
  const labels = hist.labels || [];
  chart = new Chart(ctx,{
    type:'line',
    data:{labels:labels, datasets:[
      {label:'Avg Temp', data:(hist.avgF||[]), yAxisID:'y', tension:.28, pointRadius:0, borderWidth:2},
      {label:'Avg Humidity', data:(hist.avgH||[]), yAxisID:'y1', tension:.28, pointRadius:0, borderWidth:2}
    ]},
    options:{
      responsive:true, maintainAspectRatio:false, animation:false,
      interaction:{mode:'index', intersect:false},
      scales:{
        x:{grid:{color:'rgba(255,255,255,0.03)',display:false}, ticks:{color:'#cfd6ff'}},
        y:{position:'left', title:{display:true,text:'Temp'}, ticks:{color:'#cfd6ff'}},
        y1:{position:'right', grid:{display:false}, title:{display:true,text:'Humidity'}, ticks:{color:'#cfd6ff'}}
      },
      plugins:{legend:{labels:{color:'#cfd6ff'}}}
    }
  });
  convertChartDatasets();
}

function convertTempValueFromF(f){ if(f==null) return null; if(tempUnit==='F') return f; if(tempUnit==='C') return fToC(f); if(tempUnit==='K') return fToK(f); return f; }
function convertHumValueFromPercent(h, tF=null){
  if(h==null) return null;
  if(humUnit==='%') return h;
  if(tF==null) return null;
  const tC=fToC(tF); return absHumidity(tC,h);
}

function convertChartDatasets(){
  if(!chart) return;
  const dsT=chart.data.datasets[0], dsH=chart.data.datasets[1];
  dsT.data = dsT.data.map(v => v==null?null:convertTempValueFromF(v));
  if(humUnit==='%'){ dsH.data = dsH.data.map(v => v==null?null:v); }
  else{
    dsH.data = dsH.data.map((h,i)=>{
      const tDisp = dsT.data[i]; if(tDisp==null) return null;
      let tF = tDisp;
      if(tempUnit==='C') tF=cToF(tDisp); else if(tempUnit==='K') tF=kToF(tDisp);
      return convertHumValueFromPercent(h,tF);
    });
  }
  chart.options.scales.y.title.text = (tempUnit==='F')?'Temp (°F)':(tempUnit==='C')?'Temp (°C)':'Temp (K)';
  chart.options.scales.y1.title.text = (humUnit==='%')?'RH (%)':'Abs Hum (g/m³)';
  chart.update('none');
}

function convertAllDisplays(d){
  const avgF = d?.averages?.temp_f ?? null;
  const avgH = d?.averages?.humidity ?? null;
  const avgTempConv = avgF==null?null:convertTempValueFromF(avgF);
  const avgHumConv  = avgH==null?null:convertHumValueFromPercent(avgH, avgF);
  document.getElementById('avgF').textContent = fmtNum(avgTempConv,2, tempUnit==='F'?'°F':tempUnit==='C'?'°C':'K');
  document.getElementById('avgH').textContent = fmtNum(avgHumConv,2, humUnit==='%'?'%':'g/m³');

  if(d?.sensors){
    const ss=d.sensors, temps=[ss[0].temp_f,ss[1].temp_f,ss[2].temp_f], hums=[ss[0].humidity,ss[1].humidity,ss[2].humidity];
    ['t22','ta','tb'].forEach((id,i)=>{
      const v=temps[i]; document.getElementById(id).textContent = fmtNum(v==null?null:convertTempValueFromF(v),2, tempUnit==='F'?'°F':'°C'==tempUnit?'°C':'K');
    });
    ['h22','ha','hb'].forEach((id,i)=>{
      const v=hums[i]; const conv = v==null?null:convertHumValueFromPercent(v, temps[i]);
      document.getElementById(id).textContent = fmtNum(conv,2, humUnit==='%'?'%':'g/m³');
    });
  }
  let dew=null, absH=null;
  if(avgF!=null && avgH!=null){
    const tC=fToC(avgF); const dpC=dewPointC(tC,avgH);
    dew = dpC==null?null:(tempUnit==='F'?cToF(dpC):tempUnit==='C'?dpC:dpC+273.15);
    absH = absHumidity(tC,avgH);
  }
  document.getElementById('dew').textContent   = fmtNum(dew,2, tempUnit==='F'?'°F':tempUnit==='C'?'°C':'K');
  document.getElementById('absHum').textContent= fmtNum(absH,2,'g/m³');
}

async function tick(){
  try{
    const r=await fetch('/data',{cache:'no-store'}); const d=await r.json();
    const s=d.status||{};
    document.getElementById('port').textContent = s.port||'none';
    const alert=document.getElementById('alert');
    if(s.error){ alert.textContent=s.error; alert.style.display='block'; } else { alert.style.display='none'; }
    document.getElementById('stamp').textContent = d.updated.local?`${d.updated.local} (${d.updated.ago})`:'—';
    document.getElementById('age').textContent = d.updated.ago||'no data';
    document.getElementById('ageTag').textContent = d.updated.ago||'no data';

    convertAllDisplays(d);

    if(d.updated.epoch){
      const label = new Date(d.updated.epoch*1000).toLocaleTimeString();
      const last = chart.data.labels.at(-1);
      if(!last || last!==label){
        chart.data.labels.push(label);
        chart.data.datasets[0].data.push(d.averages.temp_f);
        chart.data.datasets[1].data.push(d.averages.humidity);
        const max=300;
        if(chart.data.labels.length>max){
          const rm = chart.data.labels.length-max;
          chart.data.labels.splice(0,rm);
          chart.data.datasets.forEach(ds=>ds.data.splice(0,rm));
        }
        convertChartDatasets();
      }
    }
  }catch(err){
    console.error('tick error', err);
    const a=document.getElementById('alert'); a.textContent='Network error'; a.style.display='block';
  }
}

document.getElementById('downloadCsv').addEventListener('click', async (e)=>{
  e.preventDefault();
  const res=await fetch('/history',{cache:'no-store'}); const hist=await res.json();
  const rows=[['time','avgF(°F)','avgH(%)']];
  (hist.labels||[]).forEach((t,i)=>rows.push([t, hist.avgF?.[i]??'', hist.avgH?.[i]??'']));
  const csv=rows.map(r=>r.join(',')).join('\\n');
  const blob=new Blob([csv],{type:'text/csv'}); const url=URL.createObjectURL(blob);
  const a=document.createElement('a'); a.href=url; a.download='env-history.csv'; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
});

(async ()=>{ await initChart(); await tick(); setInterval(tick,1000); })();
</script>
</body>
</html>
"""
@app.route("/")
def index(): return render_template_string(TEMPLATE)

if __name__ == "__main__":
    threading.Thread(target=reader, daemon=True).start()
    app.run(host="0.0.0.0", port=8000)

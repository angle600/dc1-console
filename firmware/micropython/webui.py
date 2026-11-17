"""DC1 控制页（极简，省 RAM）。按钮走 /api/switch，状态 2s 拉一次。"""

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DC1</title><style>
body{font-family:-apple-system,"Microsoft YaHei",sans-serif;margin:0;padding:16px;background:#111;color:#eee}
h2{font-size:18px;margin:4px 0 12px}
.row{display:flex;justify-content:space-between;align-items:center;padding:12px;margin:8px 0;
background:#1e1e1e;border-radius:10px}
button{padding:10px 18px;border:0;border-radius:8px;font-size:15px;background:#444;color:#fff}
button.on{background:#c62828}
.mono{font-family:ui-monospace,Consolas,monospace;color:#9cdcfe}
.small{font-size:12px;color:#888;margin-top:14px;line-height:1.6}
</style></head><body>
<h2>斐讯 DC1 · MicroPython</h2>
<div id="list"></div>
<div class="small mono" id="meter">--</div>
<div class="small">接口：/api/state · /api/switch?ch=1&amp;on=1 · /api/file（PUT 上传代码）</div>
<script>
const NAMES={total:"总开关",1:"插孔1",2:"插孔2",3:"插孔3",logo:"LOGO 灯"};
function render(s){
  let h="";
  for(const k of ["total","1","2","3","logo"]){
    h+='<div class="row"><span>'+NAMES[k]+'</span><button class="'+(s[k]?"on":"")+
       '" onclick="sw(\\''+k+'\\','+(s[k]?0:1)+')">'+(s[k]?"ON":"OFF")+'</button></div>';
  }
  document.getElementById("list").innerHTML=h;
  const m=s.meter||{};
  document.getElementById("meter").textContent=
    "电压 "+(m.voltage||0)+"V  电流 "+(m.current||0)+"A  功率 "+(m.power||0)+"W";
}
function sw(ch,on){fetch("/api/switch?ch="+ch+"&on="+on).then(r=>r.json()).then(render)}
function tick(){fetch("/api/state").then(r=>r.json()).then(render).catch(()=>{})}
tick();setInterval(tick,2000);
</script></body></html>"""

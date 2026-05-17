"""Quick CLI reporter for GET /timings. Run: python show_timings.py [url]"""
import json, sys, urllib.request

url = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080") + "/timings"
try:
    d = json.loads(urllib.request.urlopen(url, timeout=5).read())
except Exception as e:
    print(f"Could not reach {url}: {e}"); sys.exit(1)

W = 50

def bar(val, maxval, width=30):
    if not maxval: return ""
    return "#" * max(1, int(val / maxval * width))

print("=" * W)
print("  BOOT PHASES")
print("=" * W)
phases = d["boot"]["phases_s"]
maxp = max(phases.values(), default=1) or 1
for phase, secs in phases.items():
    print(f"  {phase:15} {secs:7.3f}s  {bar(secs, maxp)}")
print(f"  {'TOTAL':15} {d['boot']['total_s']:7.3f}s")

print()
print("=" * W)
print("  TICK PERFORMANCE")
print("=" * W)
t = d["ticks"]
maxt = t["max_s"] or 1
print(f"  count={t['count']}  avg={t['avg_s']}s  min={t['min_s']}s  max={t['max_s']}s")
print()
for r in t["recent"]:
    print(f"  tick {r['tick']:>4}  {r['elapsed_s']:7.3f}s  {bar(r['elapsed_s'], maxt)}")

print()
print("=" * W)
print("  LLM CALL LATENCY  (per adjudication)")
print("=" * W)
l = d["llm"]
if l["count"]:
    print(f"  backend : {l['recent'][0]['backend']}")
    print(f"  calls   : {l['count']}")
    print(f"  avg     : {l['avg_s']}s")
    print(f"  min     : {l['min_s']}s")
    print(f"  max     : {l['max_s']}s")
    print()
    maxl = l["max_s"] or 1
    for r in l["recent"]:
        print(f"  {r['elapsed_s']:6.2f}s  {bar(r['elapsed_s'], maxl)}  ({r['prompt_chars']} chars)")
else:
    print("  no LLM calls recorded yet")
print()

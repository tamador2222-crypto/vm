#!/usr/bin/env python3
import urllib.request
import json
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler

HAPROXY_CSV_URL = "http://127.0.0.1:8404/stats;csv"

def fetch_csv():
    with urllib.request.urlopen(HAPROXY_CSV_URL, timeout=5) as f:
        return f.read().decode('utf-8')

def get_docker_stats(container):
    try:
        out = subprocess.run(
            ["docker", "stats", container, "--no-stream", "--format", "json"],
            capture_output=True, text=True, check=True
        )
        return json.loads(out.stdout.strip().split('\n')[0])
    except:
        return {"error": "unable to fetch"}

def compute_metrics(csv_text):
    lines = csv_text.strip().split('\n')
    if len(lines) < 2:
        return {"error": "CSV too short"}
    header = lines[0].split(',')
    idx = {}
    for i, col in enumerate(header):
        col = col.strip()
        if col.startswith('# '):
            col = col[2:]
        if col == 'pxname':
            idx['pxname'] = i
        elif col == 'svname':
            idx['svname'] = i
        elif col == 'rate':
            idx['rate'] = i
        elif col == 'rtime':
            idx['rtime'] = i
        elif col == 'status':
            idx['status'] = i
    required = ['pxname', 'svname', 'rate', 'rtime']
    for r in required:
        if r not in idx:
            return {"error": f"Column '{r}' not found in CSV. Available: {header[:20]}"}
    fog_rates = []
    fog_rtimes = []
    for line in lines[1:]:
        cols = line.split(',')
        if len(cols) <= max(idx.values()):
            continue
        px = cols[idx['pxname']].strip()
        sv = cols[idx['svname']].strip()
        if px == 'fog_nodes' and sv.startswith('fog'):
            rate_str = cols[idx['rate']]
            rate = float(rate_str) if rate_str and rate_str != '-1' else 0.0
            fog_rates.append(rate)
            rtime_str = cols[idx['rtime']]
            rtime = float(rtime_str) if rtime_str and rtime_str != '-1' else 0.0
            fog_rtimes.append(rtime)
    if not fog_rates:
        return {"error": "No fog nodes found"}
    total_rate = sum(fog_rates)
    n = len(fog_rates)
    avg_rtime_ms = sum(fog_rtimes) / n if fog_rtimes else 0.0
    mu = 400.0
    rho = total_rate / (n * mu) if mu > 0 else 0.0
    sum_x = sum(fog_rates)
    sum_sq = sum(r*r for r in fog_rates)
    jain = (sum_x**2) / (n * sum_sq) if sum_sq > 0 else 0.0
    return {
        "arrival_rate_lambda_req_per_sec": round(total_rate, 2),
        "num_fog_nodes": n,
        "fog_node_rates_req_per_sec": {f"fog{i+1}": round(r,2) for i, r in enumerate(fog_rates)},
        "avg_response_time_ms": round(avg_rtime_ms, 2),
        "utilization_rho": round(rho, 4),
        "jains_fairness_index": round(jain, 6),
        "cloud_offloading_status": "inactive" if rho < 1.0 else "active (saturated)"
    }

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/metrics':
            try:
                csv_data = fetch_csv()
                metrics = compute_metrics(csv_data)
                metrics["fog_gateway_container"] = get_docker_stats("fog-gateway")
                for i in range(1,4):
                    metrics[f"fog_node_{i}_container"] = get_docker_stats(f"fog-node-{i}")
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(metrics, indent=2).encode())
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', 8000), Handler)
    print("Metrics server running on port 8000")
    print("Access: http://<your-ip>:8000/metrics")
    server.serve_forever()

import json
from http.server import HTTPServer, BaseHTTPRequestHandler

heart_rates = {}

class FogNodeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'healthy')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != '/api/medical':
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body.decode('utf-8'))
        except:
            self.send_response(400)
            self.end_headers()
            return

        patient_id = data.get('patient_id', 'unknown')
        device_type = data.get('device_type', '')
        value = data.get('value', 0)
        systolic = data.get('systolic', 0)
        diastolic = data.get('diastolic', 0)
        spo2 = data.get('spo2', 0)

        result = {'patient_id': patient_id, 'original': data}

        if device_type == 'heart_rate_monitor':
            if patient_id not in heart_rates:
                heart_rates[patient_id] = []
            heart_rates[patient_id].append(value)
            if len(heart_rates[patient_id]) > 5:
                heart_rates[patient_id].pop(0)
            avg_hr = sum(heart_rates[patient_id]) / len(heart_rates[patient_id])
            result['avg_heart_rate'] = round(avg_hr, 1)
            result['alert'] = 'tachycardia' if value > 100 else ('bradycardia' if value < 60 else 'normal')

        elif device_type == 'blood_pressure_cuff':
            map_val = diastolic + (systolic - diastolic) / 3
            result['map'] = round(map_val, 1)
            result['alert'] = 'hypertension' if systolic > 140 or diastolic > 90 else 'normal'

        elif device_type == 'oximeter':
            result['alert'] = 'hypoxemia' if spo2 < 92 else 'normal'
            result['spo2'] = spo2

        else:
            result['alert'] = 'unknown device'

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

def run_server():
    server = HTTPServer(('0.0.0.0', 80), FogNodeHandler)
    print("Fog node processing server started on port 80")
    server.serve_forever()

if __name__ == '__main__':
    run_server()

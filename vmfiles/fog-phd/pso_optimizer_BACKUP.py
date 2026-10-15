#!/usr/bin/env python3
"""
Particle Swarm Optimization for Dynamic Fog Load Balancing
References: Gad (2022); Fahad et al. (2026); Kennedy and Eberhart (1995)
"""

import numpy as np
import subprocess
import time
import requests
import json

class Particle:
    def __init__(self, dim=3, bounds=(1, 100)):
        self.dimension = dim
        self.lower_bound = bounds[0]
        self.upper_bound = bounds[1]
        self.position = np.random.uniform(self.lower_bound, self.upper_bound, dim)
        self.velocity = np.random.uniform(-10, 10, dim)
        self.best_position = self.position.copy()
        self.best_score = float('inf')
    
    def update_position(self):
        self.position = self.position + self.velocity
        self.position = np.clip(self.position, self.lower_bound, self.upper_bound)
    
    def update_velocity(self, global_best_position, w=0.7, c1=1.5, c2=1.5):
        r1 = np.random.random(self.dimension)
        r2 = np.random.random(self.dimension)
        cognitive = c1 * r1 * (self.best_position - self.position)
        social = c2 * r2 * (global_best_position - self.position)
        self.velocity = w * self.velocity + cognitive + social

class FogPSOOptimizer:
    def __init__(self, mu=400.0, n_servers=3, swarm_size=20, iterations=30):
        self.mu = mu
        self.n = n_servers
        self.swarm_size = swarm_size
        self.iterations = iterations
        self.bounds = (1, 100)
        self.particles = [Particle(dim=n_servers, bounds=self.bounds) for _ in range(self.swarm_size)]
        self.global_best = None
        self.global_best_score = float('inf')
    
    def objective(self, weights, lam_1, lam_2, cpu_usage, mem_usage):
        w_norm = np.array(weights) / np.sum(weights)
        lam_total = lam_1 + lam_2
        
        rho_i = lam_total * w_norm / self.mu
        if np.any(rho_i >= 1):
            return 1e7
        
        P_q = self._erlang_c(lam_total)
        denom1 = lam_total * (1 - lam_1/(self.n*self.mu)) if lam_total > 0 else 0.001
        W_q1 = P_q / denom1 if denom1 > 0 else 0
        
        denom2 = lam_total * (1 - lam_1/(self.n*self.mu)) * \
                 (1 - (lam_1+lam_2)/(self.n*self.mu)) if lam_total > 0 else 0.001
        W_q2 = P_q / denom2 if denom2 > 0 else 0
        
        cpu_penalty = np.sum(np.maximum(0, np.array(cpu_usage) - 90)) / 10
        mem_penalty = np.sum(np.maximum(0, np.array(mem_usage) - 90)) / 10
        
        J = self._jains_fairness(w_norm)
        J_penalty = max(0, 0.90 - J)
        
        return (10 * W_q1 + 1 * W_q2 + 100 * cpu_penalty + 100 * mem_penalty + 100 * J_penalty)
    
    def _erlang_c(self, lam):
        rho = lam / (self.n * self.mu)
        if rho >= 1:
            return 1.0
        numerator = (3 * rho)**3 / 6
        denominator = 1 + 3*rho + (3*rho)**2/2 + (3*rho)**3/6 * 1/(1-rho)
        return min(0.99, numerator / denominator) if denominator > 0 else 1.0
    
    def _jains_fairness(self, weights):
        return (np.sum(weights)**2) / (self.n * np.sum(weights**2)) if np.sum(weights**2) > 0 else 0
    
    def optimize(self, lam_1, lam_2, cpu_usage, mem_usage):
        for _ in range(self.iterations):
            for p in self.particles:
                score = self.objective(p.position, lam_1, lam_2, cpu_usage, mem_usage)
                if score < p.best_score:
                    p.best_score = score
                    p.best_position = p.position.copy()
                if score < self.global_best_score:
                    self.global_best_score = score
                    self.global_best = p.position.copy()
            
            for p in self.particles:
                r1, r2 = np.random.random(3), np.random.random(3)
                cognitive = 1.5 * r1 * (p.best_position - p.position)
                social = 1.5 * r2 * (self.global_best - p.position)
                p.velocity = 0.7 * p.velocity + cognitive + social
                p.update_position()
        
        if self.global_best is not None:
            optimal_weights = np.array(self.global_best)
            return optimal_weights / np.sum(optimal_weights)
        return np.ones(self.n) / self.n
    
    def update_haproxy_weights(self, weights):
        for i, node in enumerate(['fog-node-1', 'fog-node-2', 'fog-node-3']):
            weight_percent = int(weights[i] * 100)
            cmd = f"echo 'set server fog_nodes/{node} weight {weight_percent}' | socat stdio /var/run/haproxy.sock"
            try:
                subprocess.run(cmd, shell=True, check=True, timeout=2)
                print(f"✅ Updated {node} to weight {weight_percent}")
            except Exception as e:
                print(f"❌ Error updating {node}: {e}")

def get_metrics():
    try:
        resp = requests.get('http://localhost:8000/metrics', timeout=5)
        return resp.json()
    except:
        return None

def get_docker_stats(container_name):
    try:
        cmd = f"docker stats {container_name} --no-stream --format '{{.CPUPerc}}'"
        cpu = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3)
        cpu_val = float(cpu.stdout.strip().replace('%', '')) if cpu.stdout else 0
        
        cmd = f"docker stats {container_name} --no-stream --format '{{.MemPerc}}'"
        mem = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3)
        mem_val = float(mem.stdout.strip().replace('%', '')) if mem.stdout else 0
        return cpu_val, mem_val
    except:
        return 0, 0

if __name__ == "__main__":
    print("🚀 Starting PSO Optimizer...")
    optimizer = FogPSOOptimizer()
    
    while True:
        try:
            metrics = get_metrics()
            if metrics and 'error' not in metrics:
                lam_1 = metrics.get('emergency_rate', 20)
                lam_2 = metrics.get('arrival_rate_lambda_req_per_sec', 100) - lam_1
                
                cpu_usage = []
                mem_usage = []
                for node in ['fog-node-1', 'fog-node-2', 'fog-node-3']:
                    cpu, mem = get_docker_stats(node)
                    cpu_usage.append(cpu)
                    mem_usage.append(mem)
                
                optimal_weights = optimizer.optimize(lam_1, lam_2, cpu_usage, mem_usage)
                print(f"📊 Optimal weights: {optimal_weights}")
                
                optimizer.update_haproxy_weights(optimal_weights)
            
        except Exception as e:
            print(f"⚠️ Error: {e}")
        
        time.sleep(5)

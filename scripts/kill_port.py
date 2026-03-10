
import os
import signal
import subprocess

def kill_port_8000():
    try:
        out = subprocess.check_output('netstat -ano | findstr :8000', shell=True).decode()
        pids = set()
        for line in out.splitlines():
            if 'LISTENING' in line or 'ESTABLISHED' in line:
                parts = line.split()
                if len(parts) >= 5:
                    pids.add(parts[-1])
        
        for pid in pids:
            try:
                print(f"Killing PID {pid}")
                subprocess.call(f'taskkill /F /PID {pid}', shell=True)
            except:
                pass
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    kill_port_8000()

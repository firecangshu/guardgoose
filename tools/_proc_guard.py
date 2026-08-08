"""临时运维脚本：列出/清理护院鹅残留进程（后端/桥接器/启动器）。"""
import subprocess
import sys

KEYWORDS = ("run_edge", "hw.bridge", "launch_guardian")


def find():
    q = ("Get-CimInstance Win32_Process | "
         "Where-Object { $_.CommandLine -match 'run_edge|hw\\.bridge|launch_guardian' } | "
         "Select-Object ProcessId, CommandLine")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", q],
                       capture_output=True, text=True)
    print(r.stdout or "(无匹配进程)")
    if r.stderr:
        print("ERR:", r.stderr[:300])


def kill():
    q = ("Get-CimInstance Win32_Process | "
         "Where-Object { $_.CommandLine -match 'run_edge|hw\\.bridge|launch_guardian' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", q],
                       capture_output=True, text=True)
    print("已停止:", r.stdout or "(无)")


if __name__ == "__main__":
    {"find": find, "kill": kill}.get(sys.argv[1] if len(sys.argv) > 1 else "find", find)()

"""快速验证 Windows TTS 是否可正常播放语音。"""
import subprocess
import sys

ps_script = r'''
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Speak("语音测试成功，请准备开始CSI检测")
'''

result = subprocess.run(
    ['powershell', '-NoProfile', '-Command', ps_script],
    capture_output=True, text=True
)
if result.returncode == 0:
    print("TTS_OK: 语音播放成功")
else:
    print(f"TTS_FAIL: {result.stderr}")
    sys.exit(1)

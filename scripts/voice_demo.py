"""使用 macOS 中文 TTS 和 ffmpeg，为本地操作录屏逐段配音。"""
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'artifacts/demo-recording/template-checkout-demo-silent.mp4'
CHAPTERS = ROOT / 'artifacts/demo-recording/chapters.json'
NARRATION = ROOT / 'docs/videos/template-checkout-demo-narration.json'
OUTPUT = ROOT / 'docs/videos/template-checkout-demo.mp4'
WORK = ROOT / 'artifacts/demo-voice'
FPS = 25


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, encoding='utf-8')


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]).stdout)


def main():
    if sys.platform != 'darwin' or not Path('/usr/bin/say').exists():
        raise SystemExit('配音需要 macOS 系统的 say 普通话音色；其他平台可直接使用已生成的有声视频。')
    if any(not shutil.which(tool) for tool in ('ffmpeg', 'ffprobe')):
        raise SystemExit('请先安装 ffmpeg（含 ffprobe 和 H.264 编码支持）。')
    if not SOURCE.is_file() or not CHAPTERS.is_file():
        raise SystemExit('缺少原始录屏或章节记录，请先运行 pnpm demo:record。')
    WORK.mkdir(parents=True, exist_ok=True)
    narration = json.loads(NARRATION.read_text(encoding='utf-8'))
    chapters = json.loads(CHAPTERS.read_text(encoding='utf-8'))
    info = probe(SOURCE)
    video = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')
    if (video['width'], video['height']) != (1440, 1000):
        raise ValueError('字幕定位对应 1440 × 1000 录屏，请用当前录制脚本重新录制。')
    # 在固定的步骤标题区域检测实际换页时间，避免浏览器录制与脚本计时的偏差。
    detection = run(['ffmpeg', '-hide_banner', '-i', str(SOURCE), '-an', '-vf',
                     "crop=1200:36:45:903,select='gt(scene,0.03)',showinfo", '-fps_mode', 'vfr', '-f', 'null', '-'])
    starts = [float(t) for t in re.findall(r'pts_time:([\d.]+)', detection.stderr)]
    if len(starts) != len(chapters) or len(starts) != len(narration['segments']):
        raise ValueError('字幕变化数量与讲解章节不一致，未覆盖现有视频。')
    if any(abs(start - chapter['seconds']) > 0.5 for start, chapter in zip(starts, chapters)):
        raise ValueError('字幕实际时间与章节记录不一致，未覆盖现有视频。')
    starts[0] = 0.0
    source_duration = float(info['format']['duration'])
    ends = starts[1:] + [source_duration]
    timeline, clips, cursor = [], [], 0.0
    for index, (start, end, segment) in enumerate(zip(starts, ends, narration['segments'])):
        number = f'{index + 1:02d}'
        speech_file, speech_text = WORK / f'{number}.aiff', WORK / f'{number}.txt'
        speech_text.write_text(segment['text'], encoding='utf-8')
        run(['/usr/bin/say', '-v', narration['voice'], '-r', str(narration['rate']),
             '-f', str(speech_text), '-o', str(speech_file)])
        speech_duration = float(probe(speech_file)['format']['duration'])
        length = end - start
        # 每段开头留 0.25 秒、末尾至少 0.35 秒；语音较长时定格本段末帧。
        target = math.ceil(max(length, speech_duration + 0.6) * FPS) / FPS
        extra = max(0, target - length) + 0.08
        clip = WORK / f'{number}.mkv'
        filters = (f'[0:v]setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={extra:.3f},'
                   f'trim=duration={target:.3f},fps={FPS},format=yuv420p[v];'
                   f'[1:a]aresample=48000,asetpts=PTS-STARTPTS,'
                   f'afade=t=in:d=0.015,afade=t=out:st={max(0, speech_duration - 0.06):.3f}:d=0.06,'
                   f'loudnorm=I=-16:TP=-1.5:LRA=7,adelay=250:all=1,apad,atrim=duration={target:.3f}[a]')
        run(['ffmpeg', '-v', 'error', '-y', '-ss', f'{start:.3f}', '-t', f'{length:.3f}', '-i', str(SOURCE),
             '-i', str(speech_file), '-filter_complex', filters, '-map', '[v]', '-map', '[a]',
             '-c:v', 'libx264', '-preset', 'fast', '-crf', '22', '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '1', str(clip)])
        clips.append(clip)
        timeline.append(dict(title=segment['title'], text=segment['text'],
                             source_start=start, source_end=end, output_start=round(cursor, 3),
                             output_end=round(cursor + target, 3), speech_start=round(cursor + 0.25, 3),
                             speech_end=round(cursor + 0.25 + speech_duration, 3),
                             hold_seconds=round(max(0, target - length), 3)))
        cursor += target
        print(f'{number} {segment["title"]}：语音 {speech_duration:.2f} 秒，画面 {target:.2f} 秒', flush=True)
    playlist = WORK / 'concat.txt'
    playlist.write_text(''.join("file '" + str(path).replace("'", "'\\''") + "'\n" for path in clips), encoding='utf-8')
    temporary = WORK / 'template-checkout-demo-voiced.mp4'
    run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(playlist),
         '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'copy', '-af', 'volume=-1.5dB', '-c:a', 'aac', '-b:a', '128k',
         '-movflags', '+faststart', str(temporary)])
    result = probe(temporary)
    streams = {stream['codec_type']: stream for stream in result['streams']}
    if streams.get('audio', {}).get('codec_name') != 'aac' or streams.get('video', {}).get('codec_name') != 'h264':
        raise ValueError('音视频编码检查失败，未覆盖现有视频。')
    if abs(float(streams['video']['duration']) - float(streams['audio']['duration'])) > 0.15:
        raise ValueError('音视频时长不一致，未覆盖现有视频。')
    run(['ffmpeg', '-v', 'error', '-i', str(temporary), '-f', 'null', '-'])
    temporary.replace(OUTPUT)
    (WORK / 'timeline.json').write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'有声视频已生成：{OUTPUT}（{float(result["format"]["duration"]):.2f} 秒）')


if __name__ == '__main__':
    main()

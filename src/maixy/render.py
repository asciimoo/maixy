import io
import re
import textwrap

from .config import NORMAL, WORKING, WAITING, DONE


def render(pane, status='idle', page=0):
    from PIL import Image, ImageDraw, ImageFont
    bg = {'done': DONE, 'working': WORKING, 'waiting': WAITING,
          'idle': NORMAL}.get(status, '#202833')
    foreground = '#000000' if status in ('working', 'waiting', 'done') else '#ffffff'
    img = Image.new('RGB', (118, 118), bg if pane else '#000000')
    d = ImageDraw.Draw(img)
    from pathlib import Path
    fontpath = next((p for p in (
        '/System/Library/Fonts/Supplemental/Arial.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf',
    ) if Path(p).exists()), None)
    boldpath = next((p for p in (
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf',
    ) if Path(p).exists()), fontpath)
    def font(size, bold=False):
        selected = boldpath if bold else fontpath
        return ImageFont.truetype(selected, size) if selected else ImageFont.load_default(size=size)
    if pane:
        count = pane.get('background_count', 0)
        badge = '+{}'.format(count) if count else ''
        header_width = 106 - (d.textlength(badge, font=font(11, bold=True)) + 6 if badge else 0)
        agent = pane['agent'].upper()
        while len(agent) > 1 and d.textlength(agent, font=font(11)) > header_width:
            agent = agent[:-2] + '…'
        d.text((7, 6), agent, fill=foreground, font=font(11))
        if badge:
            d.text((111, 6), badge, fill=foreground, font=font(11, bold=True), anchor='ra')
        window = pane['window_name']
        while d.textlength(window, font=font(9)) > 106:
            window = window[:-2] + '…'
        d.text((59, 18), window, fill=foreground, font=font(9), anchor='mt')
        title = re.sub(r'\s+', ' ', pane['pane_title']).strip() or pane['window_name']
        # Strip animated agent spinner prefix, while retaining the pane name.
        title = re.sub(r'^[✳✻✽✶✢⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s*', '', title)
        lines = textwrap.wrap(title, width=13, break_long_words=True) or ['Agent']
        for i, line in enumerate(lines[:4]):
            if i == 3 and len(lines) > 4:
                line = line[:11] + '…'
            while d.textlength(line, font=font(14, bold=True)) > 106:
                line = line[:-2] + '…'
            d.text((59, 28 + i * 16), line, fill=foreground, font=font(14, bold=True), anchor='mt')
        footer = {'done': 'FINISHED', 'working': 'WORKING', 'waiting': 'NEEDS INPUT',
                  'idle': 'READY'}.get(status, 'UNKNOWN')
        footer = footer[:17]
        d.text((59, 101), footer, fill=foreground, font=font(11, bold=True), anchor='mt')
    else:
        d.text((59, 52), '—', fill='#354254', font=font(20), anchor='mt')
    stream = io.BytesIO()
    img.save(stream, 'JPEG', quality=95, subsampling=0)
    return stream.getvalue()

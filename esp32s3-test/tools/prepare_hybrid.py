"""Generate the initial gallery and private firmware settings at CMake configure time."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from face_database import FaceDatabase
from hybrid_gallery import gallery

p = argparse.ArgumentParser()
p.add_argument('--database', required=True)
p.add_argument('--config', required=True)
p.add_argument('--output', required=True)
p.add_argument('--quality', required=True)
a = p.parse_args()
config = json.loads(Path(a.config).read_text()) if Path(a.config).exists() else {}
output = Path(a.output)
output.mkdir(parents=True, exist_ok=True)
binary, _, _ = gallery(FaceDatabase(a.database), config.get('threshold', .5), config.get('match_margin', .05))
(output/'edge_gallery.bin').write_bytes(binary)
keys = {'wifi_ssid':'', 'wifi_password':'', 'server_url':'', 'api_token':''}
lines = ['#pragma once']
for key, default in keys.items():
    value = config.get(key, default)
    if not isinstance(value, str) or '\0' in value or '\n' in value or '\r' in value:
        raise ValueError(f'Invalid {key}')
    lines.append(f'static constexpr const char *EDGE_{key.upper()} = {json.dumps(value)};')
if len(config.get('wifi_ssid', '').encode()) > 32 or len(config.get('wifi_password', '').encode()) > 63:
    raise ValueError('Wi-Fi SSID/password too long')
(output/'edge_settings.h').write_text('\n'.join(lines)+'\n')

import math
quality = json.loads(Path(a.quality).read_text())
ranges = {
    'min_width_ratio': (.05, .8), 'min_height_ratio': (.05, .9),
    'border_ratio': (0, .1), 'min_detection_score': (0, 1),
    'min_eye_distance_ratio': (.01, .3), 'max_roll_degrees': (1, 45),
    'max_yaw_ratio': (.01, .5), 'min_nose_height_ratio': (0, .6),
    'max_nose_height_ratio': (.4, 1),
}
for key, (lower, upper) in ranges.items():
    value = quality.get(key)
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f'Invalid quality threshold: {key}')
if quality['min_nose_height_ratio'] >= quality['max_nose_height_ratio']:
    raise ValueError('Nose height thresholds out of order')
if quality['min_width_ratio'] >= 1-2*quality['border_ratio'] or quality['min_height_ratio'] >= 1-2*quality['border_ratio']:
    raise ValueError('Face size thresholds conflict with border margin')
lines = ['#pragma once', '#include "face_quality.h"', 'static constexpr face_quality::Config FACE_QUALITY_CONFIG = {']
lines += [f'    .{key} = {float(quality[key])}f,' for key in ranges]
lines += ['};']
(output/'face_quality_settings.h').write_text('\n'.join(lines)+'\n')

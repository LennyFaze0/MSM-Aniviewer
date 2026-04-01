"""
Sprite Renderer
Pure rendering logic for drawing sprites with OpenGL
Separated from widget logic for clarity and testability
"""

import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple, Any
from OpenGL.GL import *
import numpy as np
from PIL import Image

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception as heif_exc:  # pragma: no cover - optional dependency
    print(f"Warning: Failed to enable HEIF/AVIF support in renderer: {heif_exc}")

from core.data_structures import LayerData, SpriteInfo
from core.texture_atlas import TextureAtlas
from core.animation_player import AnimationPlayer
from utils.shader_registry import ShaderPreset, ShaderRegistry, ShaderBehavior


def _set_texture_2d_enabled(enabled: bool) -> None:
    """Guard GL_TEXTURE_2D capability toggles on strict/core contexts."""
    try:
        if enabled:
            glEnable(GL_TEXTURE_2D)
        else:
            glDisable(GL_TEXTURE_2D)
    except Exception:
        return


_BICUBIC_VERTEX_SHADER = """
#version 120
void main() {
    gl_TexCoord[0] = gl_MultiTexCoord0;
    gl_FrontColor = gl_Color;
    gl_Position = ftransform();
}
"""

_ALPHA_STENCIL_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_texture;
uniform float u_alphaThreshold;
uniform float u_alphaSoftness;
uniform vec2 u_texelSize;
uniform vec2 u_uvMin;
uniform vec2 u_uvMax;

vec2 clamp_uv(vec2 uv) {
    return clamp(uv, u_uvMin, u_uvMax);
}

float alpha_at(vec2 uv) {
    return texture2D(u_texture, clamp_uv(uv)).a;
}

void main() {
    vec2 uv = clamp_uv(gl_TexCoord[0].xy);
    float alpha = alpha_at(uv);
    float radius = max(0.0, u_alphaSoftness);
    if (radius > 0.0) {
        vec2 o = u_texelSize * radius;
        float a0 = alpha_at(uv + vec2( o.x, 0.0));
        float a1 = alpha_at(uv + vec2(-o.x, 0.0));
        float a2 = alpha_at(uv + vec2(0.0,  o.y));
        float a3 = alpha_at(uv + vec2(0.0, -o.y));
        float a4 = alpha_at(uv + vec2( o.x,  o.y));
        float a5 = alpha_at(uv + vec2( o.x, -o.y));
        float a6 = alpha_at(uv + vec2(-o.x,  o.y));
        float a7 = alpha_at(uv + vec2(-o.x, -o.y));
        float maxA = max(alpha, max(max(max(a0, a1), max(a2, a3)), max(max(a4, a5), max(a6, a7))));
        float blend = clamp(radius * 2.2, 0.0, 1.0);
        alpha = mix(alpha, maxA, blend);
    }
    if (alpha <= u_alphaThreshold) {
        discard;
    }
    gl_FragColor = vec4(1.0);
}
"""

_MASK_CONSUMER_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_texture;
uniform sampler2D u_maskTexture;
uniform vec2 u_viewSize;
void main() {
    vec4 base = texture2D(u_texture, gl_TexCoord[0].xy) * gl_Color;
    vec2 uv = gl_FragCoord.xy / max(u_viewSize, vec2(1.0));
    float mask_alpha = texture2D(u_maskTexture, uv).a;
    float keep = clamp(1.0 - mask_alpha, 0.0, 1.0);
    base.rgb *= keep;
    base.a *= keep;
    if (base.a <= 0.0001) {
        discard;
    }
    gl_FragColor = base;
}
"""

_BICUBIC_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_texture;
uniform vec2 u_texSize;
uniform float u_strength;

float mitchell(float x) {
    x = abs(x);
    float B = 1.0 / 3.0;
    float C = 1.0 / 3.0;
    if (x < 1.0) {
        return ((12.0 - 9.0 * B - 6.0 * C) * x * x * x
              + (-18.0 + 12.0 * B + 6.0 * C) * x * x
              + (6.0 - 2.0 * B)) / 6.0;
    }
    if (x < 2.0) {
        return ((-B - 6.0 * C) * x * x * x
              + (6.0 * B + 30.0 * C) * x * x
              + (-12.0 * B - 48.0 * C) * x
              + (8.0 * B + 24.0 * C)) / 6.0;
    }
    return 0.0;
}

vec4 sample_bicubic(vec2 uv) {
    vec2 tex = uv * u_texSize - 0.5;
    vec2 base = floor(tex);
    vec2 f = tex - base;
    vec4 sum = vec4(0.0);
    float wsum = 0.0;
    for (int j = -1; j <= 2; ++j) {
        float wj = mitchell(f.y - float(j));
        for (int i = -1; i <= 2; ++i) {
            float wi = mitchell(f.x - float(i));
            float w = wi * wj;
            vec2 coord = (base + vec2(float(i), float(j)) + 0.5) / u_texSize;
            sum += texture2D(u_texture, coord) * w;
            wsum += w;
        }
    }
    if (wsum > 0.0) {
        sum /= wsum;
    }
    return sum;
}

vec4 sample_bilinear(vec2 uv) {
    vec2 tex = uv * u_texSize - 0.5;
    vec2 base = floor(tex);
    vec2 f = tex - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / u_texSize;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / u_texSize;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / u_texSize;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / u_texSize;
    vec4 c00 = texture2D(u_texture, coord00);
    vec4 c10 = texture2D(u_texture, coord10);
    vec4 c01 = texture2D(u_texture, coord01);
    vec4 c11 = texture2D(u_texture, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    return mix(cx0, cx1, f.y);
}

void main() {
    vec4 filtered = sample_bicubic(gl_TexCoord[0].st);
    vec4 linear = sample_bilinear(gl_TexCoord[0].st);
    float strength = clamp(u_strength, 0.0, 1.0);
    vec4 color = mix(linear, filtered, strength);
    gl_FragColor = color * gl_Color;
}
"""

_BICUBIC_ALPHA_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_texture;
uniform vec2 u_texSize;
uniform float u_strength;

float mitchell(float x) {
    x = abs(x);
    float B = 1.0 / 3.0;
    float C = 1.0 / 3.0;
    if (x < 1.0) {
        return ((12.0 - 9.0 * B - 6.0 * C) * x * x * x
              + (-18.0 + 12.0 * B + 6.0 * C) * x * x
              + (6.0 - 2.0 * B)) / 6.0;
    }
    if (x < 2.0) {
        return ((-B - 6.0 * C) * x * x * x
              + (6.0 * B + 30.0 * C) * x * x
              + (-12.0 * B - 48.0 * C) * x
              + (8.0 * B + 24.0 * C)) / 6.0;
    }
    return 0.0;
}

vec4 sample_bicubic_alpha(vec2 uv) {
    vec2 tex = uv * u_texSize - 0.5;
    vec2 base = floor(tex);
    vec2 f = tex - base;
    vec3 rgb_sum = vec3(0.0);
    float a_sum = 0.0;
    float wsum = 0.0;
    for (int j = -1; j <= 2; ++j) {
        float wj = mitchell(f.y - float(j));
        for (int i = -1; i <= 2; ++i) {
            float wi = mitchell(f.x - float(i));
            float w = wi * wj;
            vec2 coord = (base + vec2(float(i), float(j)) + 0.5) / u_texSize;
            vec4 sample = texture2D(u_texture, coord);
            float a = sample.a;
            vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
            rgb_sum += rgb * w;
            a_sum += a * w;
            wsum += w;
        }
    }
    if (wsum > 0.0) {
        rgb_sum /= wsum;
        a_sum /= wsum;
    }
    a_sum = clamp(a_sum, 0.0, 1.0);
    vec3 premul = rgb_sum * a_sum;
    return vec4(premul, a_sum);
}

vec4 sample_bilinear_alpha(vec2 uv) {
    vec2 tex = uv * u_texSize - 0.5;
    vec2 base = floor(tex);
    vec2 f = tex - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / u_texSize;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / u_texSize;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / u_texSize;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / u_texSize;
    vec4 c00 = texture2D(u_texture, coord00);
    vec4 c10 = texture2D(u_texture, coord10);
    vec4 c01 = texture2D(u_texture, coord01);
    vec4 c11 = texture2D(u_texture, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    vec4 sample = mix(cx0, cx1, f.y);
    float a = sample.a;
    vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
    vec3 premul = rgb * a;
    return vec4(premul, a);
}

void main() {
    vec4 filtered = sample_bicubic_alpha(gl_TexCoord[0].st);
    vec4 linear = sample_bilinear_alpha(gl_TexCoord[0].st);
    float strength = clamp(u_strength, 0.0, 1.0);
    vec4 color = mix(linear, filtered, strength);
    gl_FragColor = color * gl_Color;
}
"""

_LANCZOS_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_texture;
uniform vec2 u_texSize;
uniform float u_strength;

const float PI = 3.14159265358979323846;

float sinc(float x) {
    if (x == 0.0) {
        return 1.0;
    }
    float px = x * PI;
    return sin(px) / px;
}

float lanczos(float x, float a) {
    x = abs(x);
    if (x >= a) {
        return 0.0;
    }
    return sinc(x) * sinc(x / a);
}

vec4 sample_lanczos(vec2 uv) {
    vec2 tex = uv * u_texSize - 0.5;
    vec2 base = floor(tex);
    vec2 f = tex - base;
    vec4 sum = vec4(0.0);
    float wsum = 0.0;
    for (int j = -2; j <= 3; ++j) {
        float wj = lanczos(f.y - float(j), 3.0);
        for (int i = -2; i <= 3; ++i) {
            float wi = lanczos(f.x - float(i), 3.0);
            float w = wi * wj;
            vec2 coord = (base + vec2(float(i), float(j)) + 0.5) / u_texSize;
            sum += texture2D(u_texture, coord) * w;
            wsum += w;
        }
    }
    if (wsum > 0.0) {
        sum /= wsum;
    }
    return sum;
}

vec4 sample_bilinear(vec2 uv) {
    vec2 tex = uv * u_texSize - 0.5;
    vec2 base = floor(tex);
    vec2 f = tex - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / u_texSize;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / u_texSize;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / u_texSize;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / u_texSize;
    vec4 c00 = texture2D(u_texture, coord00);
    vec4 c10 = texture2D(u_texture, coord10);
    vec4 c01 = texture2D(u_texture, coord01);
    vec4 c11 = texture2D(u_texture, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    return mix(cx0, cx1, f.y);
}

void main() {
    vec4 filtered = sample_lanczos(gl_TexCoord[0].st);
    vec4 linear = sample_bilinear(gl_TexCoord[0].st);
    float strength = clamp(u_strength, 0.0, 1.0);
    vec4 color = mix(linear, filtered, strength);
    gl_FragColor = color * gl_Color;
}
"""

_LANCZOS_ALPHA_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_texture;
uniform vec2 u_texSize;
uniform float u_strength;

const float PI = 3.14159265358979323846;

float sinc(float x) {
    if (x == 0.0) {
        return 1.0;
    }
    float px = x * PI;
    return sin(px) / px;
}

float lanczos(float x, float a) {
    x = abs(x);
    if (x >= a) {
        return 0.0;
    }
    return sinc(x) * sinc(x / a);
}

vec4 sample_lanczos_alpha(vec2 uv) {
    vec2 tex = uv * u_texSize - 0.5;
    vec2 base = floor(tex);
    vec2 f = tex - base;
    vec3 rgb_sum = vec3(0.0);
    float a_sum = 0.0;
    float wsum = 0.0;
    for (int j = -2; j <= 3; ++j) {
        float wj = lanczos(f.y - float(j), 3.0);
        for (int i = -2; i <= 3; ++i) {
            float wi = lanczos(f.x - float(i), 3.0);
            float w = wi * wj;
            vec2 coord = (base + vec2(float(i), float(j)) + 0.5) / u_texSize;
            vec4 sample = texture2D(u_texture, coord);
            float a = sample.a;
            vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
            rgb_sum += rgb * w;
            a_sum += a * w;
            wsum += w;
        }
    }
    if (wsum > 0.0) {
        rgb_sum /= wsum;
        a_sum /= wsum;
    }
    a_sum = clamp(a_sum, 0.0, 1.0);
    vec3 premul = rgb_sum * a_sum;
    return vec4(premul, a_sum);
}

vec4 sample_bilinear_alpha(vec2 uv) {
    vec2 tex = uv * u_texSize - 0.5;
    vec2 base = floor(tex);
    vec2 f = tex - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / u_texSize;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / u_texSize;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / u_texSize;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / u_texSize;
    vec4 c00 = texture2D(u_texture, coord00);
    vec4 c10 = texture2D(u_texture, coord10);
    vec4 c01 = texture2D(u_texture, coord01);
    vec4 c11 = texture2D(u_texture, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    vec4 sample = mix(cx0, cx1, f.y);
    float a = sample.a;
    vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
    vec3 premul = rgb * a;
    return vec4(premul, a);
}

void main() {
    vec4 filtered = sample_lanczos_alpha(gl_TexCoord[0].st);
    vec4 linear = sample_bilinear_alpha(gl_TexCoord[0].st);
    float strength = clamp(u_strength, 0.0, 1.0);
    vec4 color = mix(linear, filtered, strength);
    gl_FragColor = color * gl_Color;
}
"""

_DOF_SMOOTH_ALPHA_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_texture;
uniform vec2 u_texSize;
uniform float u_strength;

vec4 sample_bilinear_alpha(vec2 uv) {
    vec2 tex = uv * u_texSize - 0.5;
    vec2 base = floor(tex);
    vec2 f = tex - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / u_texSize;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / u_texSize;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / u_texSize;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / u_texSize;
    vec4 c00 = texture2D(u_texture, coord00);
    vec4 c10 = texture2D(u_texture, coord10);
    vec4 c01 = texture2D(u_texture, coord01);
    vec4 c11 = texture2D(u_texture, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    vec4 sample = mix(cx0, cx1, f.y);
    float a = sample.a;
    vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
    vec3 premul = rgb * a;
    return vec4(premul, a);
}

vec3 to_straight(vec4 sample) {
    return (sample.a > 0.0) ? (sample.rgb / sample.a) : vec3(0.0);
}

void main() {
    vec2 uv = gl_TexCoord[0].st;
    vec4 s0 = sample_bilinear_alpha(uv);
    float a0 = s0.a;
    vec2 texel = vec2(1.0 / max(1.0, u_texSize.x), 1.0 / max(1.0, u_texSize.y));

    vec4 s_n = sample_bilinear_alpha(uv + vec2(0.0, texel.y));
    vec4 s_s = sample_bilinear_alpha(uv - vec2(0.0, texel.y));
    vec4 s_e = sample_bilinear_alpha(uv + vec2(texel.x, 0.0));
    vec4 s_w = sample_bilinear_alpha(uv - vec2(texel.x, 0.0));
    vec4 s_ne = sample_bilinear_alpha(uv + vec2(texel.x, texel.y));
    vec4 s_nw = sample_bilinear_alpha(uv + vec2(-texel.x, texel.y));
    vec4 s_se = sample_bilinear_alpha(uv + vec2(texel.x, -texel.y));
    vec4 s_sw = sample_bilinear_alpha(uv + vec2(-texel.x, -texel.y));
    vec4 s_n2 = sample_bilinear_alpha(uv + vec2(0.0, texel.y * 2.0));
    vec4 s_s2 = sample_bilinear_alpha(uv - vec2(0.0, texel.y * 2.0));
    vec4 s_e2 = sample_bilinear_alpha(uv + vec2(texel.x * 2.0, 0.0));
    vec4 s_w2 = sample_bilinear_alpha(uv - vec2(texel.x * 2.0, 0.0));
    vec4 s_ne2 = sample_bilinear_alpha(uv + vec2(texel.x * 2.0, texel.y * 2.0));
    vec4 s_nw2 = sample_bilinear_alpha(uv + vec2(-texel.x * 2.0, texel.y * 2.0));
    vec4 s_se2 = sample_bilinear_alpha(uv + vec2(texel.x * 2.0, -texel.y * 2.0));
    vec4 s_sw2 = sample_bilinear_alpha(uv + vec2(-texel.x * 2.0, -texel.y * 2.0));

    float a_n = s_n.a;
    float a_s = s_s.a;
    float a_e = s_e.a;
    float a_w = s_w.a;
    float a_ne = s_ne.a;
    float a_nw = s_nw.a;
    float a_se = s_se.a;
    float a_sw = s_sw.a;
    float a_n2 = s_n2.a;
    float a_s2 = s_s2.a;
    float a_e2 = s_e2.a;
    float a_w2 = s_w2.a;
    float a_ne2 = s_ne2.a;
    float a_nw2 = s_nw2.a;
    float a_se2 = s_se2.a;
    float a_sw2 = s_sw2.a;

    // 3x3 blur keeps detail while softening stair-step corners.
    float blur1 = a0 * 0.40
                + (a_n + a_s + a_e + a_w) * 0.10
                + (a_ne + a_nw + a_se + a_sw) * 0.05;
    // 5x5 extension provides stronger smoothing at higher strengths.
    float blur2 = blur1 * 0.70
                + (a_n2 + a_s2 + a_e2 + a_w2) * 0.05
                + (a_ne2 + a_nw2 + a_se2 + a_sw2) * 0.025;

    float a_min = min(a0, min(min(min(a_n, a_s), min(a_e, a_w)), min(min(a_ne, a_nw), min(a_se, a_sw))));
    float a_max = max(a0, max(max(max(a_n, a_s), max(a_e, a_w)), max(max(a_ne, a_nw), max(a_se, a_sw))));
    float contrast = a_max - a_min;
    // Edge detect from alpha contrast so fully-opaque edge pixels still smooth.
    float edge_factor = smoothstep(0.03, 0.40, contrast);
    float strength = clamp(u_strength, 0.0, 1.0);
    float smooth_target = mix(blur1, blur2, strength);
    float a_soft = mix(a0, smooth_target, strength * edge_factor);
    a_soft = clamp(a_soft, 0.0, 1.0);

    vec3 rgb0 = to_straight(s0);
    vec3 rgb_blur1 = rgb0 * 0.40
                   + (to_straight(s_n) + to_straight(s_s) + to_straight(s_e) + to_straight(s_w)) * 0.10
                   + (to_straight(s_ne) + to_straight(s_nw) + to_straight(s_se) + to_straight(s_sw)) * 0.05;
    vec3 rgb_blur2 = rgb_blur1 * 0.70
                   + (to_straight(s_n2) + to_straight(s_s2) + to_straight(s_e2) + to_straight(s_w2)) * 0.05
                   + (to_straight(s_ne2) + to_straight(s_nw2) + to_straight(s_se2) + to_straight(s_sw2)) * 0.025;
    vec3 rgb_target = mix(rgb_blur1, rgb_blur2, strength);
    vec3 rgb_soft = mix(rgb0, rgb_target, strength * edge_factor);
    vec3 premul = rgb_soft * a_soft;
    gl_FragColor = vec4(premul, a_soft) * gl_Color;
}
"""

_DOF_SMOOTH_ALPHA_STRONG_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_texture;
uniform vec2 u_texSize;
uniform float u_strength;

vec4 sample_bilinear_alpha(vec2 uv) {
    vec2 tex = uv * u_texSize - 0.5;
    vec2 base = floor(tex);
    vec2 f = tex - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / u_texSize;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / u_texSize;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / u_texSize;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / u_texSize;
    vec4 c00 = texture2D(u_texture, coord00);
    vec4 c10 = texture2D(u_texture, coord10);
    vec4 c01 = texture2D(u_texture, coord01);
    vec4 c11 = texture2D(u_texture, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    vec4 sample = mix(cx0, cx1, f.y);
    float a = sample.a;
    vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
    vec3 premul = rgb * a;
    return vec4(premul, a);
}

vec3 to_straight(vec4 sample) {
    return (sample.a > 0.0) ? (sample.rgb / sample.a) : vec3(0.0);
}

void main() {
    vec2 uv = gl_TexCoord[0].st;
    vec4 s0 = sample_bilinear_alpha(uv);
    float a0 = s0.a;
    vec2 texel = vec2(1.0 / max(1.0, u_texSize.x), 1.0 / max(1.0, u_texSize.y));

    vec4 s_n = sample_bilinear_alpha(uv + vec2(0.0, texel.y));
    vec4 s_s = sample_bilinear_alpha(uv - vec2(0.0, texel.y));
    vec4 s_e = sample_bilinear_alpha(uv + vec2(texel.x, 0.0));
    vec4 s_w = sample_bilinear_alpha(uv - vec2(texel.x, 0.0));
    vec4 s_ne = sample_bilinear_alpha(uv + vec2(texel.x, texel.y));
    vec4 s_nw = sample_bilinear_alpha(uv + vec2(-texel.x, texel.y));
    vec4 s_se = sample_bilinear_alpha(uv + vec2(texel.x, -texel.y));
    vec4 s_sw = sample_bilinear_alpha(uv + vec2(-texel.x, -texel.y));
    vec4 s_n2 = sample_bilinear_alpha(uv + vec2(0.0, texel.y * 2.0));
    vec4 s_s2 = sample_bilinear_alpha(uv - vec2(0.0, texel.y * 2.0));
    vec4 s_e2 = sample_bilinear_alpha(uv + vec2(texel.x * 2.0, 0.0));
    vec4 s_w2 = sample_bilinear_alpha(uv - vec2(texel.x * 2.0, 0.0));
    vec4 s_ne2 = sample_bilinear_alpha(uv + vec2(texel.x * 2.0, texel.y * 2.0));
    vec4 s_nw2 = sample_bilinear_alpha(uv + vec2(-texel.x * 2.0, texel.y * 2.0));
    vec4 s_se2 = sample_bilinear_alpha(uv + vec2(texel.x * 2.0, -texel.y * 2.0));
    vec4 s_sw2 = sample_bilinear_alpha(uv + vec2(-texel.x * 2.0, -texel.y * 2.0));
    vec4 s_n3 = sample_bilinear_alpha(uv + vec2(0.0, texel.y * 3.0));
    vec4 s_s3 = sample_bilinear_alpha(uv - vec2(0.0, texel.y * 3.0));
    vec4 s_e3 = sample_bilinear_alpha(uv + vec2(texel.x * 3.0, 0.0));
    vec4 s_w3 = sample_bilinear_alpha(uv - vec2(texel.x * 3.0, 0.0));

    float a_n = s_n.a;
    float a_s = s_s.a;
    float a_e = s_e.a;
    float a_w = s_w.a;
    float a_ne = s_ne.a;
    float a_nw = s_nw.a;
    float a_se = s_se.a;
    float a_sw = s_sw.a;
    float a_n2 = s_n2.a;
    float a_s2 = s_s2.a;
    float a_e2 = s_e2.a;
    float a_w2 = s_w2.a;
    float a_ne2 = s_ne2.a;
    float a_nw2 = s_nw2.a;
    float a_se2 = s_se2.a;
    float a_sw2 = s_sw2.a;
    float a_n3 = s_n3.a;
    float a_s3 = s_s3.a;
    float a_e3 = s_e3.a;
    float a_w3 = s_w3.a;

    float blur1 = a0 * 0.30
                + (a_n + a_s + a_e + a_w) * 0.10
                + (a_ne + a_nw + a_se + a_sw) * 0.05;
    float blur2 = blur1 * 0.65
                + (a_n2 + a_s2 + a_e2 + a_w2) * 0.05
                + (a_ne2 + a_nw2 + a_se2 + a_sw2) * 0.01875;
    float blur3 = blur2 * 0.75
                + (a_n3 + a_s3 + a_e3 + a_w3) * 0.0625;

    float a_min = min(a0, min(min(min(a_n, a_s), min(a_e, a_w)), min(min(a_ne, a_nw), min(a_se, a_sw))));
    float a_max = max(a0, max(max(max(a_n, a_s), max(a_e, a_w)), max(max(a_ne, a_nw), max(a_se, a_sw))));
    float contrast = a_max - a_min;
    float edge_factor = smoothstep(0.02, 0.30, contrast);
    float strength = clamp(u_strength, 0.0, 1.0);
    float smooth_target = mix(blur2, blur3, strength);
    float a_soft = mix(a0, smooth_target, strength * edge_factor);
    a_soft = clamp(a_soft, 0.0, 1.0);

    vec3 rgb0 = to_straight(s0);
    vec3 rgb_blur1 = rgb0 * 0.30
                   + (to_straight(s_n) + to_straight(s_s) + to_straight(s_e) + to_straight(s_w)) * 0.10
                   + (to_straight(s_ne) + to_straight(s_nw) + to_straight(s_se) + to_straight(s_sw)) * 0.05;
    vec3 rgb_blur2 = rgb_blur1 * 0.65
                   + (to_straight(s_n2) + to_straight(s_s2) + to_straight(s_e2) + to_straight(s_w2)) * 0.05
                   + (to_straight(s_ne2) + to_straight(s_nw2) + to_straight(s_se2) + to_straight(s_sw2)) * 0.01875;
    vec3 rgb_blur3 = rgb_blur2 * 0.75
                   + (to_straight(s_n3) + to_straight(s_s3) + to_straight(s_e3) + to_straight(s_w3)) * 0.0625;
    vec3 rgb_target = mix(rgb_blur2, rgb_blur3, strength);
    vec3 rgb_soft = mix(rgb0, rgb_target, strength * edge_factor);
    vec3 premul = rgb_soft * a_soft;
    gl_FragColor = vec4(premul, a_soft) * gl_Color;
}
"""

_DOF_SMOOTH_MASK_ALPHA_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_overlay;
uniform sampler2D u_mask;
uniform vec2 u_overlaySize;
uniform vec2 u_maskSize;
uniform float u_strength;

vec4 sample_bilinear_alpha(sampler2D tex, vec2 size, vec2 uv) {
    vec2 texel = uv * size - 0.5;
    vec2 base = floor(texel);
    vec2 f = texel - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / size;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / size;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / size;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / size;
    vec4 c00 = texture2D(tex, coord00);
    vec4 c10 = texture2D(tex, coord10);
    vec4 c01 = texture2D(tex, coord01);
    vec4 c11 = texture2D(tex, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    vec4 sample = mix(cx0, cx1, f.y);
    float a = sample.a;
    vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
    vec3 premul = rgb * a;
    return vec4(premul, a);
}

float smooth_mask_alpha(vec2 uv) {
    float a0 = sample_bilinear_alpha(u_mask, u_maskSize, uv).a;
    vec2 texel = vec2(1.0 / max(1.0, u_maskSize.x), 1.0 / max(1.0, u_maskSize.y));

    float a_n = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(0.0, texel.y)).a;
    float a_s = sample_bilinear_alpha(u_mask, u_maskSize, uv - vec2(0.0, texel.y)).a;
    float a_e = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x, 0.0)).a;
    float a_w = sample_bilinear_alpha(u_mask, u_maskSize, uv - vec2(texel.x, 0.0)).a;
    float a_ne = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x, texel.y)).a;
    float a_nw = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(-texel.x, texel.y)).a;
    float a_se = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x, -texel.y)).a;
    float a_sw = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(-texel.x, -texel.y)).a;
    float a_n2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(0.0, texel.y * 2.0)).a;
    float a_s2 = sample_bilinear_alpha(u_mask, u_maskSize, uv - vec2(0.0, texel.y * 2.0)).a;
    float a_e2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x * 2.0, 0.0)).a;
    float a_w2 = sample_bilinear_alpha(u_mask, u_maskSize, uv - vec2(texel.x * 2.0, 0.0)).a;
    float a_ne2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x * 2.0, texel.y * 2.0)).a;
    float a_nw2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(-texel.x * 2.0, texel.y * 2.0)).a;
    float a_se2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x * 2.0, -texel.y * 2.0)).a;
    float a_sw2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(-texel.x * 2.0, -texel.y * 2.0)).a;

    float blur1 = a0 * 0.40
                + (a_n + a_s + a_e + a_w) * 0.10
                + (a_ne + a_nw + a_se + a_sw) * 0.05;
    float blur2 = blur1 * 0.70
                + (a_n2 + a_s2 + a_e2 + a_w2) * 0.05
                + (a_ne2 + a_nw2 + a_se2 + a_sw2) * 0.025;

    float a_min = min(a0, min(min(min(a_n, a_s), min(a_e, a_w)), min(min(a_ne, a_nw), min(a_se, a_sw))));
    float a_max = max(a0, max(max(max(a_n, a_s), max(a_e, a_w)), max(max(a_ne, a_nw), max(a_se, a_sw))));
    float contrast = a_max - a_min;
    float edge_factor = smoothstep(0.03, 0.40, contrast);
    float strength = clamp(u_strength, 0.0, 1.0);
    float smooth_target = mix(blur1, blur2, strength);
    float a_soft = mix(a0, smooth_target, strength * edge_factor);
    return clamp(a_soft, 0.0, 1.0);
}

void main() {
    vec4 overlay = sample_bilinear_alpha(u_overlay, u_overlaySize, gl_TexCoord[0].st);
    float mask_a = smooth_mask_alpha(gl_TexCoord[1].st);
    vec3 rgb = overlay.rgb * gl_Color.rgb * mask_a;
    float out_alpha = clamp(overlay.a * mask_a, 0.0, 1.0);
    gl_FragColor = vec4(rgb, out_alpha);
}
"""

_DOF_SMOOTH_MASK_ALPHA_STRONG_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_overlay;
uniform sampler2D u_mask;
uniform vec2 u_overlaySize;
uniform vec2 u_maskSize;
uniform float u_strength;

vec4 sample_bilinear_alpha(sampler2D tex, vec2 size, vec2 uv) {
    vec2 texel = uv * size - 0.5;
    vec2 base = floor(texel);
    vec2 f = texel - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / size;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / size;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / size;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / size;
    vec4 c00 = texture2D(tex, coord00);
    vec4 c10 = texture2D(tex, coord10);
    vec4 c01 = texture2D(tex, coord01);
    vec4 c11 = texture2D(tex, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    vec4 sample = mix(cx0, cx1, f.y);
    float a = sample.a;
    vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
    vec3 premul = rgb * a;
    return vec4(premul, a);
}

float smooth_mask_alpha(vec2 uv) {
    float a0 = sample_bilinear_alpha(u_mask, u_maskSize, uv).a;
    vec2 texel = vec2(1.0 / max(1.0, u_maskSize.x), 1.0 / max(1.0, u_maskSize.y));

    float a_n = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(0.0, texel.y)).a;
    float a_s = sample_bilinear_alpha(u_mask, u_maskSize, uv - vec2(0.0, texel.y)).a;
    float a_e = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x, 0.0)).a;
    float a_w = sample_bilinear_alpha(u_mask, u_maskSize, uv - vec2(texel.x, 0.0)).a;
    float a_ne = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x, texel.y)).a;
    float a_nw = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(-texel.x, texel.y)).a;
    float a_se = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x, -texel.y)).a;
    float a_sw = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(-texel.x, -texel.y)).a;
    float a_n2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(0.0, texel.y * 2.0)).a;
    float a_s2 = sample_bilinear_alpha(u_mask, u_maskSize, uv - vec2(0.0, texel.y * 2.0)).a;
    float a_e2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x * 2.0, 0.0)).a;
    float a_w2 = sample_bilinear_alpha(u_mask, u_maskSize, uv - vec2(texel.x * 2.0, 0.0)).a;
    float a_ne2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x * 2.0, texel.y * 2.0)).a;
    float a_nw2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(-texel.x * 2.0, texel.y * 2.0)).a;
    float a_se2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x * 2.0, -texel.y * 2.0)).a;
    float a_sw2 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(-texel.x * 2.0, -texel.y * 2.0)).a;
    float a_n3 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(0.0, texel.y * 3.0)).a;
    float a_s3 = sample_bilinear_alpha(u_mask, u_maskSize, uv - vec2(0.0, texel.y * 3.0)).a;
    float a_e3 = sample_bilinear_alpha(u_mask, u_maskSize, uv + vec2(texel.x * 3.0, 0.0)).a;
    float a_w3 = sample_bilinear_alpha(u_mask, u_maskSize, uv - vec2(texel.x * 3.0, 0.0)).a;

    float blur1 = a0 * 0.30
                + (a_n + a_s + a_e + a_w) * 0.10
                + (a_ne + a_nw + a_se + a_sw) * 0.05;
    float blur2 = blur1 * 0.65
                + (a_n2 + a_s2 + a_e2 + a_w2) * 0.05
                + (a_ne2 + a_nw2 + a_se2 + a_sw2) * 0.01875;
    float blur3 = blur2 * 0.75
                + (a_n3 + a_s3 + a_e3 + a_w3) * 0.0625;

    float a_min = min(a0, min(min(min(a_n, a_s), min(a_e, a_w)), min(min(a_ne, a_nw), min(a_se, a_sw))));
    float a_max = max(a0, max(max(max(a_n, a_s), max(a_e, a_w)), max(max(a_ne, a_nw), max(a_se, a_sw))));
    float contrast = a_max - a_min;
    float edge_factor = smoothstep(0.02, 0.30, contrast);
    float strength = clamp(u_strength, 0.0, 1.0);
    float smooth_target = mix(blur2, blur3, strength);
    float a_soft = mix(a0, smooth_target, strength * edge_factor);
    return clamp(a_soft, 0.0, 1.0);
}

void main() {
    vec4 overlay = sample_bilinear_alpha(u_overlay, u_overlaySize, gl_TexCoord[0].st);
    float mask_a = smooth_mask_alpha(gl_TexCoord[1].st);
    vec3 rgb = overlay.rgb * gl_Color.rgb * mask_a;
    float out_alpha = clamp(overlay.a * mask_a, 0.0, 1.0);
    gl_FragColor = vec4(rgb, out_alpha);
}
"""

_BICUBIC_MASK_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_overlay;
uniform sampler2D u_mask;
uniform vec2 u_overlaySize;
uniform vec2 u_maskSize;
uniform float u_strength;

float mitchell(float x) {
    x = abs(x);
    float B = 1.0 / 3.0;
    float C = 1.0 / 3.0;
    if (x < 1.0) {
        return ((12.0 - 9.0 * B - 6.0 * C) * x * x * x
              + (-18.0 + 12.0 * B + 6.0 * C) * x * x
              + (6.0 - 2.0 * B)) / 6.0;
    }
    if (x < 2.0) {
        return ((-B - 6.0 * C) * x * x * x
              + (6.0 * B + 30.0 * C) * x * x
              + (-12.0 * B - 48.0 * C) * x
              + (8.0 * B + 24.0 * C)) / 6.0;
    }
    return 0.0;
}

vec4 sample_bicubic(sampler2D tex, vec2 size, vec2 uv) {
    vec2 texel = uv * size - 0.5;
    vec2 base = floor(texel);
    vec2 f = texel - base;
    vec4 sum = vec4(0.0);
    float wsum = 0.0;
    for (int j = -1; j <= 2; ++j) {
        float wj = mitchell(f.y - float(j));
        for (int i = -1; i <= 2; ++i) {
            float wi = mitchell(f.x - float(i));
            float w = wi * wj;
            vec2 coord = (base + vec2(float(i), float(j)) + 0.5) / size;
            sum += texture2D(tex, coord) * w;
            wsum += w;
        }
    }
    if (wsum > 0.0) {
        sum /= wsum;
    }
    return sum;
}

vec4 sample_bilinear(sampler2D tex, vec2 size, vec2 uv) {
    vec2 texel = uv * size - 0.5;
    vec2 base = floor(texel);
    vec2 f = texel - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / size;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / size;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / size;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / size;
    vec4 c00 = texture2D(tex, coord00);
    vec4 c10 = texture2D(tex, coord10);
    vec4 c01 = texture2D(tex, coord01);
    vec4 c11 = texture2D(tex, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    return mix(cx0, cx1, f.y);
}

void main() {
    float strength = clamp(u_strength, 0.0, 1.0);
    vec4 overlay_filtered = sample_bicubic(u_overlay, u_overlaySize, gl_TexCoord[0].st);
    vec4 overlay_linear = sample_bilinear(u_overlay, u_overlaySize, gl_TexCoord[0].st);
    vec4 overlay = mix(overlay_linear, overlay_filtered, strength);
    vec4 mask_filtered = sample_bicubic(u_mask, u_maskSize, gl_TexCoord[1].st);
    vec4 mask_linear = sample_bilinear(u_mask, u_maskSize, gl_TexCoord[1].st);
    vec4 mask = mix(mask_linear, mask_filtered, strength);
    float mask_a = mask.a;
    vec3 rgb = overlay.rgb * gl_Color.rgb * mask_a;
    gl_FragColor = vec4(rgb, mask_a);
}
"""

_BICUBIC_MASK_ALPHA_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_overlay;
uniform sampler2D u_mask;
uniform vec2 u_overlaySize;
uniform vec2 u_maskSize;
uniform float u_strength;

float mitchell(float x) {
    x = abs(x);
    float B = 1.0 / 3.0;
    float C = 1.0 / 3.0;
    if (x < 1.0) {
        return ((12.0 - 9.0 * B - 6.0 * C) * x * x * x
              + (-18.0 + 12.0 * B + 6.0 * C) * x * x
              + (6.0 - 2.0 * B)) / 6.0;
    }
    if (x < 2.0) {
        return ((-B - 6.0 * C) * x * x * x
              + (6.0 * B + 30.0 * C) * x * x
              + (-12.0 * B - 48.0 * C) * x
              + (8.0 * B + 24.0 * C)) / 6.0;
    }
    return 0.0;
}

vec4 sample_bicubic_alpha(sampler2D tex, vec2 size, vec2 uv) {
    vec2 texel = uv * size - 0.5;
    vec2 base = floor(texel);
    vec2 f = texel - base;
    vec3 rgb_sum = vec3(0.0);
    float a_sum = 0.0;
    float wsum = 0.0;
    for (int j = -1; j <= 2; ++j) {
        float wj = mitchell(f.y - float(j));
        for (int i = -1; i <= 2; ++i) {
            float wi = mitchell(f.x - float(i));
            float w = wi * wj;
            vec2 coord = (base + vec2(float(i), float(j)) + 0.5) / size;
            vec4 sample = texture2D(tex, coord);
            float a = sample.a;
            vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
            rgb_sum += rgb * w;
            a_sum += a * w;
            wsum += w;
        }
    }
    if (wsum > 0.0) {
        rgb_sum /= wsum;
        a_sum /= wsum;
    }
    a_sum = clamp(a_sum, 0.0, 1.0);
    vec3 premul = rgb_sum * a_sum;
    return vec4(premul, a_sum);
}

vec4 sample_bilinear_alpha(sampler2D tex, vec2 size, vec2 uv) {
    vec2 texel = uv * size - 0.5;
    vec2 base = floor(texel);
    vec2 f = texel - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / size;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / size;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / size;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / size;
    vec4 c00 = texture2D(tex, coord00);
    vec4 c10 = texture2D(tex, coord10);
    vec4 c01 = texture2D(tex, coord01);
    vec4 c11 = texture2D(tex, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    vec4 sample = mix(cx0, cx1, f.y);
    float a = sample.a;
    vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
    vec3 premul = rgb * a;
    return vec4(premul, a);
}

void main() {
    float strength = clamp(u_strength, 0.0, 1.0);
    vec4 overlay_filtered = sample_bicubic_alpha(u_overlay, u_overlaySize, gl_TexCoord[0].st);
    vec4 overlay_linear = sample_bilinear_alpha(u_overlay, u_overlaySize, gl_TexCoord[0].st);
    vec4 overlay = mix(overlay_linear, overlay_filtered, strength);
    vec4 mask_filtered = sample_bicubic_alpha(u_mask, u_maskSize, gl_TexCoord[1].st);
    vec4 mask_linear = sample_bilinear_alpha(u_mask, u_maskSize, gl_TexCoord[1].st);
    vec4 mask = mix(mask_linear, mask_filtered, strength);
    float mask_a = mask.a;
    vec3 rgb = overlay.rgb * gl_Color.rgb * mask_a;
    gl_FragColor = vec4(rgb, mask_a);
}
"""

_LANCZOS_MASK_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_overlay;
uniform sampler2D u_mask;
uniform vec2 u_overlaySize;
uniform vec2 u_maskSize;
uniform float u_strength;

const float PI = 3.14159265358979323846;

float sinc(float x) {
    if (x == 0.0) {
        return 1.0;
    }
    float px = x * PI;
    return sin(px) / px;
}

float lanczos(float x, float a) {
    x = abs(x);
    if (x >= a) {
        return 0.0;
    }
    return sinc(x) * sinc(x / a);
}

vec4 sample_lanczos(sampler2D tex, vec2 size, vec2 uv) {
    vec2 texel = uv * size - 0.5;
    vec2 base = floor(texel);
    vec2 f = texel - base;
    vec4 sum = vec4(0.0);
    float wsum = 0.0;
    for (int j = -2; j <= 3; ++j) {
        float wj = lanczos(f.y - float(j), 3.0);
        for (int i = -2; i <= 3; ++i) {
            float wi = lanczos(f.x - float(i), 3.0);
            float w = wi * wj;
            vec2 coord = (base + vec2(float(i), float(j)) + 0.5) / size;
            sum += texture2D(tex, coord) * w;
            wsum += w;
        }
    }
    if (wsum > 0.0) {
        sum /= wsum;
    }
    return sum;
}

vec4 sample_bilinear(sampler2D tex, vec2 size, vec2 uv) {
    vec2 texel = uv * size - 0.5;
    vec2 base = floor(texel);
    vec2 f = texel - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / size;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / size;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / size;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / size;
    vec4 c00 = texture2D(tex, coord00);
    vec4 c10 = texture2D(tex, coord10);
    vec4 c01 = texture2D(tex, coord01);
    vec4 c11 = texture2D(tex, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    return mix(cx0, cx1, f.y);
}

void main() {
    float strength = clamp(u_strength, 0.0, 1.0);
    vec4 overlay_filtered = sample_lanczos(u_overlay, u_overlaySize, gl_TexCoord[0].st);
    vec4 overlay_linear = sample_bilinear(u_overlay, u_overlaySize, gl_TexCoord[0].st);
    vec4 overlay = mix(overlay_linear, overlay_filtered, strength);
    vec4 mask_filtered = sample_lanczos(u_mask, u_maskSize, gl_TexCoord[1].st);
    vec4 mask_linear = sample_bilinear(u_mask, u_maskSize, gl_TexCoord[1].st);
    vec4 mask = mix(mask_linear, mask_filtered, strength);
    float mask_a = mask.a;
    vec3 rgb = overlay.rgb * gl_Color.rgb * mask_a;
    gl_FragColor = vec4(rgb, mask_a);
}
"""

_LANCZOS_MASK_ALPHA_FRAGMENT_SHADER = """
#version 120
uniform sampler2D u_overlay;
uniform sampler2D u_mask;
uniform vec2 u_overlaySize;
uniform vec2 u_maskSize;
uniform float u_strength;

const float PI = 3.14159265358979323846;

float sinc(float x) {
    if (x == 0.0) {
        return 1.0;
    }
    float px = x * PI;
    return sin(px) / px;
}

float lanczos(float x, float a) {
    x = abs(x);
    if (x >= a) {
        return 0.0;
    }
    return sinc(x) * sinc(x / a);
}

vec4 sample_lanczos_alpha(sampler2D tex, vec2 size, vec2 uv) {
    vec2 texel = uv * size - 0.5;
    vec2 base = floor(texel);
    vec2 f = texel - base;
    vec3 rgb_sum = vec3(0.0);
    float a_sum = 0.0;
    float wsum = 0.0;
    for (int j = -2; j <= 3; ++j) {
        float wj = lanczos(f.y - float(j), 3.0);
        for (int i = -2; i <= 3; ++i) {
            float wi = lanczos(f.x - float(i), 3.0);
            float w = wi * wj;
            vec2 coord = (base + vec2(float(i), float(j)) + 0.5) / size;
            vec4 sample = texture2D(tex, coord);
            float a = sample.a;
            vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
            rgb_sum += rgb * w;
            a_sum += a * w;
            wsum += w;
        }
    }
    if (wsum > 0.0) {
        rgb_sum /= wsum;
        a_sum /= wsum;
    }
    a_sum = clamp(a_sum, 0.0, 1.0);
    vec3 premul = rgb_sum * a_sum;
    return vec4(premul, a_sum);
}

vec4 sample_bilinear_alpha(sampler2D tex, vec2 size, vec2 uv) {
    vec2 texel = uv * size - 0.5;
    vec2 base = floor(texel);
    vec2 f = texel - base;
    vec2 coord00 = (base + vec2(0.0, 0.0) + 0.5) / size;
    vec2 coord10 = (base + vec2(1.0, 0.0) + 0.5) / size;
    vec2 coord01 = (base + vec2(0.0, 1.0) + 0.5) / size;
    vec2 coord11 = (base + vec2(1.0, 1.0) + 0.5) / size;
    vec4 c00 = texture2D(tex, coord00);
    vec4 c10 = texture2D(tex, coord10);
    vec4 c01 = texture2D(tex, coord01);
    vec4 c11 = texture2D(tex, coord11);
    vec4 cx0 = mix(c00, c10, f.x);
    vec4 cx1 = mix(c01, c11, f.x);
    vec4 sample = mix(cx0, cx1, f.y);
    float a = sample.a;
    vec3 rgb = (a > 0.0) ? (sample.rgb / a) : vec3(0.0);
    vec3 premul = rgb * a;
    return vec4(premul, a);
}

void main() {
    float strength = clamp(u_strength, 0.0, 1.0);
    vec4 overlay_filtered = sample_lanczos_alpha(u_overlay, u_overlaySize, gl_TexCoord[0].st);
    vec4 overlay_linear = sample_bilinear_alpha(u_overlay, u_overlaySize, gl_TexCoord[0].st);
    vec4 overlay = mix(overlay_linear, overlay_filtered, strength);
    vec4 mask_filtered = sample_lanczos_alpha(u_mask, u_maskSize, gl_TexCoord[1].st);
    vec4 mask_linear = sample_bilinear_alpha(u_mask, u_maskSize, gl_TexCoord[1].st);
    vec4 mask = mix(mask_linear, mask_filtered, strength);
    float mask_a = mask.a;
    vec3 rgb = overlay.rgb * gl_Color.rgb * mask_a;
    gl_FragColor = vec4(rgb, mask_a);
}
"""


# Blend mode constants matching MSM game engine
# From sys::gfx::OpenGLState::BlendMode::realSet in FunctionsAll.txt
class BlendMode:
    """
    Blend mode enum matching MSM game engine values.
    
    Mapping derived from sys::gfx::OpenGLState::BlendMode::realSet in FunctionsAll.txt.
    The engine supports a handful of blend ids that ultimately boil down to a few
    glBlendFunc combinations:
        - default/0    -> GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA
        - 1/3/4        -> GL_ONE, GL_ONE_MINUS_SRC_ALPHA (premult alpha)
        - 2            -> additive (GL_ONE, GL_ONE)
        - 5            -> inherit/leave current GL state
        - 6            -> multiply (GL_DST_COLOR, GL_ONE)
        - 7            -> screen-ish (GL_DST_COLOR, GL_ONE_MINUS_SRC_ALPHA)
    
    We keep textures in premultiplied form, so we treat the default mode the same
    as the premult paths inside the viewer to match MSM visuals.
    """
    STANDARD = 0
    PREMULT_ALPHA = 1
    ADDITIVE = 2
    PREMULT_ALPHA_ALT = 3
    PREMULT_ALPHA_ALT2 = 4
    INHERIT = 5
    MULTIPLY = 6
    SCREEN = 7
    STRAIGHT_ALPHA = 8


def set_blend_mode(blend_mode: int):
    """
    Set OpenGL blend function based on blend mode value.
    
    This matches the MSM game engine's blend mode handling.
    The game uses premultiplied alpha textures, so the blend functions
    are designed for that format.
    
    Args:
        blend_mode: Blend mode value from layer data
    """
    if blend_mode in (BlendMode.STANDARD,
                      BlendMode.PREMULT_ALPHA,
                      BlendMode.PREMULT_ALPHA_ALT,
                      BlendMode.PREMULT_ALPHA_ALT2,
                      BlendMode.INHERIT):
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
    elif blend_mode == BlendMode.ADDITIVE:
        glBlendFunc(GL_ONE, GL_ONE)
    elif blend_mode == BlendMode.MULTIPLY:
        glBlendFunc(GL_DST_COLOR, GL_ONE)
    elif blend_mode == BlendMode.SCREEN:
        glBlendFunc(GL_DST_COLOR, GL_ONE_MINUS_SRC_ALPHA)
    elif blend_mode == BlendMode.STRAIGHT_ALPHA:
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    elif blend_mode == BlendMode.INHERIT:
        return
    else:
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)


def reset_blend_mode():
    """Reset to default blend mode (premultiplied alpha)"""
    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)


@dataclass
class SpriteDrawInfo:
    """Capture sprite geometry and color data for shader overlays."""

    vertices: List[Tuple[float, float]]
    texcoords: List[Tuple[float, float]]
    triangles: List[int]
    color: Tuple[float, float, float, float]
    sprite: SpriteInfo
    atlas: TextureAtlas


@dataclass
class ShaderOverlayTexture:
    """Simple container for shader sequence textures."""

    path: str
    texture_id: Optional[int] = None
    width: int = 0
    height: int = 0

    def ensure_loaded(self) -> bool:
        if self.texture_id:
            return True
        try:
            image = Image.open(self.path)
            image = image.convert('RGBA')
            img_data = np.array(image, dtype=np.float32) / 255.0
            alpha = img_data[:, :, 3:4]
            img_data[:, :, 0:3] *= alpha
            img_data = (img_data * 255.0).astype(np.uint8)
            self.texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self.texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, image.width, image.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, img_data)
            self.width = image.width
            self.height = image.height
            return True
        except Exception as exc:
            print(f"Failed to load shader texture '{self.path}': {exc}")
            return False


class SpriteRenderer:
    """
    Handles sprite rendering logic
    Separated from OpenGL widget for better organization
    """
    
    def __init__(self):
        self.position_scale: float = 1.0
        self.base_world_scale: float = 1.0  # Set to 1.0 - JSON coordinates are already correct
        self.enable_logging: bool = False
        self.log_data: list = []
        self.anchor_bias_x: float = 0.0
        self.anchor_bias_y: float = 0.0
        self.anchor_flip_x: bool = False
        self.anchor_flip_y: bool = False
        self.anchor_scale_x: float = 1.0
        self.anchor_scale_y: float = 1.0
        self.local_position_multiplier: float = 1.0
        self.parent_mix: float = 1.0
        self.rotation_bias: float = 0.0
        self.scale_bias_x: float = 1.0
        self.scale_bias_y: float = 1.0
        self.world_offset_x: float = 0.0
        self.world_offset_y: float = 0.0
        self.trim_shift_multiplier: float = 1.0
        self.anchor_overrides: Dict[int, Tuple[float, float]] = {}
        self.shader_registry: Optional[ShaderRegistry] = None
        self._missing_shader_warnings: Set[str] = set()
        self._sprite_alias_cache: Dict[Tuple[str, str], str] = {}
        self._sprite_alias_warnings: Set[Tuple[str, str]] = set()
        self.shader_texture_cache: Dict[str, ShaderOverlayTexture] = {}
        self.current_time: float = 0.0
        self.animation_duration: float = 0.0
        self.costume_pivot_adjustment_enabled: bool = False
        self.pending_mask_key: Optional[str] = None
        self._mask_warning_messages: Set[str] = set()
        self._cycle_warning_keys: Set[Tuple[int, int]] = set()
        self._anchor_map_warnings: Set[Tuple[str, str]] = set()
        self.texture_filter_mode: str = "bilinear"
        self.texture_filter_strength: float = 1.0
        self.dof_alpha_smoothing_enabled: bool = False
        self.dof_alpha_smoothing_strength: float = 0.5
        self.dof_alpha_smoothing_mode: str = "normal"
        self.dof_sprite_shader_mode: str = "auto"
        self._texture_filter_cache: Dict[int, str] = {}
        self._texture_mipmap_cache: Set[int] = set()
        self._anisotropy_supported: Optional[bool] = None
        self._max_anisotropy: float = 1.0
        self._filter_programs: Dict[str, int] = {}
        self._filter_uniforms: Dict[str, Dict[str, int]] = {}
        self._alpha_stencil_program: int = 0
        self._alpha_stencil_uniform_texture: int = -1
        self._alpha_stencil_uniform_threshold: int = -1
        self._alpha_stencil_uniform_softness: int = -1
        self._alpha_stencil_uniform_texel_size: int = -1
        self._alpha_stencil_uniform_uv_min: int = -1
        self._alpha_stencil_uniform_uv_max: int = -1
        self._mask_consumer_program: int = 0
        self._mask_consumer_uniform_texture: int = -1
        self._mask_consumer_uniform_mask: int = -1
        self._mask_consumer_uniform_view_size: int = -1
        self._mask_fbo: Optional[int] = None
        self._mask_texture: Optional[int] = None
        self._mask_size: Tuple[int, int] = (0, 0)
        self._mask_capture_mode: str = "none"
        self.viewport_size_hint: Tuple[int, int] = (1, 1)
        self.framebuffer_binding_hint: int = 0
        # Ignore ultra-low alpha texels when stamping stencil to reduce AVIF fringe artifacts.
        self.mask_stencil_alpha_threshold: float = 0.06
        # Small prefilter radius (in texel units) for alpha->stencil conversion.
        self.mask_stencil_alpha_softness: float = 0.45
        # Temporary mask diagnostics (off by default).
        debug_flag = os.environ.get("ANIVIEWER_MASK_DEBUG", "0").strip().lower()
        self.mask_debug_enabled: bool = debug_flag not in {"0", "false", "off", "no"}
        self.mask_debug_target: str = (
            os.environ.get("ANIVIEWER_MASK_DEBUG_TARGET", "monster_gjlm").strip().lower()
        )
        self._mask_debug_frame_index: int = 0
        self._mask_debug_draw_order: int = 0
        self._mask_debug_frame_active: bool = False
        self.fast_preview_enabled: bool = False

    def set_fast_preview_enabled(self, enabled: bool) -> None:
        self.fast_preview_enabled = bool(enabled)

    def _normalize_texture_filter_mode(self, mode: Optional[str]) -> str:
        normalized = (mode or "").strip().lower()
        aliases = {
            "linear": "bilinear",
            "linear_mipmap_linear": "trilinear",
            "tri": "trilinear",
            "trilinear_mipmap": "trilinear",
            "cubic": "bicubic",
            "bicubic_mipmap": "bicubic",
            "lanczos_mipmap": "lanczos",
            "dof_soft_alpha": "dof_smooth_alpha",
            "nearest_mipmap": "nearest_mipmap_nearest",
            "linear_mipmap": "linear_mipmap_linear",
            "mipmap_nearest": "nearest_mipmap_nearest",
            "mipmap_linear": "linear_mipmap_linear",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in {
            "nearest",
            "bilinear",
            "nearest_mipmap_nearest",
            "linear_mipmap_nearest",
            "nearest_mipmap_linear",
            "trilinear",
            "bicubic",
            "lanczos",
            "dof_smooth_alpha",
            "dof_smooth_alpha_strong",
        } and not normalized.startswith("anisotropic_"):
            normalized = "bilinear"
        return normalized

    def set_texture_filter_mode(self, mode: Optional[str]) -> str:
        """Update sprite texture filtering and clear cached GL state."""
        normalized = self._normalize_texture_filter_mode(mode)
        if normalized != self.texture_filter_mode:
            self.texture_filter_mode = normalized
            self._texture_filter_cache.clear()
        return normalized

    def set_texture_filter_strength(self, strength: Optional[float]) -> float:
        """Set the shader filter blend strength (0.0-1.0) for bicubic/lanczos modes."""
        try:
            value = float(strength)
        except (TypeError, ValueError):
            value = 1.0
        value = max(0.0, min(1.0, value))
        self.texture_filter_strength = value
        return self.texture_filter_strength

    def set_dof_alpha_smoothing_enabled(self, enabled: bool) -> bool:
        self.dof_alpha_smoothing_enabled = bool(enabled)
        return self.dof_alpha_smoothing_enabled

    def set_dof_alpha_smoothing_strength(self, strength: Optional[float]) -> float:
        try:
            value = float(strength)
        except (TypeError, ValueError):
            value = 0.5
        value = max(0.0, min(1.0, value))
        self.dof_alpha_smoothing_strength = value
        return self.dof_alpha_smoothing_strength

    def set_dof_alpha_smoothing_mode(self, mode: Optional[str]) -> str:
        normalized = (mode or "").strip().lower()
        if normalized not in {"normal", "strong"}:
            normalized = "normal"
        self.dof_alpha_smoothing_mode = normalized
        return self.dof_alpha_smoothing_mode

    def set_dof_sprite_shader_mode(self, mode: Optional[str]) -> str:
        normalized = (mode or "").strip().lower()
        if normalized not in {
            "auto",
            "anim2d",
            "dawnoffire_unlit",
            "sprites_default",
            "unlit_transparent",
            "unlit_transparent_masked",
        }:
            normalized = "auto"
        self.dof_sprite_shader_mode = normalized
        return self.dof_sprite_shader_mode

    def _resolve_effective_shader_name(
        self,
        state: Optional[Dict[str, Any]],
        atlases: Optional[List[TextureAtlas]],
        default_shader_name: Optional[str] = None,
    ) -> Optional[str]:
        shader_name = default_shader_name
        if shader_name is None and state:
            shader_name = state.get("shader")

        mode = self.dof_sprite_shader_mode
        if mode == "auto" or not state or not atlases:
            return shader_name

        sprite_name = state.get("sprite_name")
        if not sprite_name:
            return shader_name
        _, atlas, _ = self._find_sprite_in_atlases(sprite_name, atlases)
        if not atlas or getattr(atlas, "pivot_mode", None) != "dof":
            return shader_name

        forced_shader = {
            "anim2d": "Anim2D/Normal+Alpha",
            "dawnoffire_unlit": "DawnOfFire/UnlitShader",
            "sprites_default": "Sprites/Default",
            "unlit_transparent": "Unlit/Transparent",
            "unlit_transparent_masked": "Unlit/Transparent Masked",
        }.get(mode)
        return forced_shader or shader_name

    def _resolve_anisotropy_support(self) -> None:
        if self._anisotropy_supported is not None:
            return
        try:
            max_value = glGetFloatv(GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT)
            if isinstance(max_value, (list, tuple, np.ndarray)):
                max_value = max_value[0] if max_value else 1.0
            self._max_anisotropy = float(max_value or 1.0)
            self._anisotropy_supported = self._max_anisotropy > 1.0
        except Exception:
            self._anisotropy_supported = False
            self._max_anisotropy = 1.0

    def _apply_texture_filter(self, texture_id: Optional[int], mode_override: Optional[str] = None) -> None:
        if not texture_id:
            return
        mode = self._normalize_texture_filter_mode(mode_override) if mode_override else self.texture_filter_mode
        cached = self._texture_filter_cache.get(int(texture_id))
        if cached == mode:
            return
        wants_mipmap = False
        anisotropy = None
        if mode == "nearest":
            min_filter = GL_NEAREST
            mag_filter = GL_NEAREST
        elif mode == "bilinear":
            min_filter = GL_LINEAR
            mag_filter = GL_LINEAR
        elif mode == "nearest_mipmap_nearest":
            min_filter = GL_NEAREST_MIPMAP_NEAREST
            mag_filter = GL_NEAREST
            wants_mipmap = True
        elif mode == "linear_mipmap_nearest":
            min_filter = GL_LINEAR_MIPMAP_NEAREST
            mag_filter = GL_LINEAR
            wants_mipmap = True
        elif mode == "nearest_mipmap_linear":
            min_filter = GL_NEAREST_MIPMAP_LINEAR
            mag_filter = GL_NEAREST
            wants_mipmap = True
        elif mode == "trilinear":
            min_filter = GL_LINEAR_MIPMAP_LINEAR
            mag_filter = GL_LINEAR
            wants_mipmap = True
        elif mode in ("bicubic", "lanczos"):
            # Shader-based filtering samples raw texels.
            min_filter = GL_NEAREST
            mag_filter = GL_NEAREST
        elif mode in ("dof_smooth_alpha", "dof_smooth_alpha_strong"):
            # Edge smoothing shader samples neighboring texels explicitly.
            min_filter = GL_NEAREST
            mag_filter = GL_NEAREST
        elif mode.startswith("anisotropic_"):
            min_filter = GL_LINEAR_MIPMAP_LINEAR
            mag_filter = GL_LINEAR
            wants_mipmap = True
            try:
                anisotropy = float(mode.split("_", 1)[1].replace("x", ""))
            except Exception:
                anisotropy = None
        else:
            min_filter = GL_LINEAR
            mag_filter = GL_LINEAR
        if wants_mipmap and int(texture_id) not in self._texture_mipmap_cache:
            try:
                glGenerateMipmap(GL_TEXTURE_2D)
            except Exception:
                # Fall back to bilinear if mipmap generation fails.
                min_filter = GL_LINEAR
                mag_filter = GL_LINEAR
                wants_mipmap = False
            self._texture_mipmap_cache.add(int(texture_id))
        if anisotropy:
            self._resolve_anisotropy_support()
            if self._anisotropy_supported and self._max_anisotropy > 1.0:
                level = max(1.0, min(float(anisotropy), float(self._max_anisotropy)))
                try:
                    glTexParameterf(GL_TEXTURE_2D, GL_TEXTURE_MAX_ANISOTROPY_EXT, level)
                except Exception:
                    pass
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, min_filter)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, mag_filter)
        self._texture_filter_cache[int(texture_id)] = mode

    def _compile_filter_program(self, fragment_source: str) -> int:
        try:
            vert = glCreateShader(GL_VERTEX_SHADER)
            glShaderSource(vert, _BICUBIC_VERTEX_SHADER)
            glCompileShader(vert)
            if glGetShaderiv(vert, GL_COMPILE_STATUS) != GL_TRUE:
                info = glGetShaderInfoLog(vert).decode("utf-8", "ignore")
                print(f"[Renderer] Vertex shader compile failed: {info}")
                glDeleteShader(vert)
                return 0
            frag = glCreateShader(GL_FRAGMENT_SHADER)
            glShaderSource(frag, fragment_source)
            glCompileShader(frag)
            if glGetShaderiv(frag, GL_COMPILE_STATUS) != GL_TRUE:
                info = glGetShaderInfoLog(frag).decode("utf-8", "ignore")
                print(f"[Renderer] Fragment shader compile failed: {info}")
                glDeleteShader(frag)
                glDeleteShader(vert)
                return 0
            program = glCreateProgram()
            glAttachShader(program, vert)
            glAttachShader(program, frag)
            glLinkProgram(program)
            if glGetProgramiv(program, GL_LINK_STATUS) != GL_TRUE:
                info = glGetProgramInfoLog(program).decode("utf-8", "ignore")
                print(f"[Renderer] Shader link failed: {info}")
                glDeleteProgram(program)
                glDeleteShader(vert)
                glDeleteShader(frag)
                return 0
            glDeleteShader(vert)
            glDeleteShader(frag)
            return int(program)
        except Exception as exc:
            print(f"[Renderer] Shader compile exception: {exc}")
            return 0

    def _get_filter_program(self, mode: str) -> int:
        cached = self._filter_programs.get(mode)
        if cached is not None:
            return cached
        if mode == "bicubic":
            program = self._compile_filter_program(_BICUBIC_FRAGMENT_SHADER)
        elif mode == "bicubic_alpha":
            program = self._compile_filter_program(_BICUBIC_ALPHA_FRAGMENT_SHADER)
        elif mode == "lanczos":
            program = self._compile_filter_program(_LANCZOS_FRAGMENT_SHADER)
        elif mode == "lanczos_alpha":
            program = self._compile_filter_program(_LANCZOS_ALPHA_FRAGMENT_SHADER)
        elif mode == "dof_smooth_alpha":
            program = self._compile_filter_program(_DOF_SMOOTH_ALPHA_FRAGMENT_SHADER)
        elif mode == "dof_smooth_alpha_strong":
            program = self._compile_filter_program(_DOF_SMOOTH_ALPHA_STRONG_FRAGMENT_SHADER)
        elif mode == "dof_smooth_alpha_mask":
            program = self._compile_filter_program(_DOF_SMOOTH_MASK_ALPHA_FRAGMENT_SHADER)
        elif mode == "dof_smooth_alpha_strong_mask":
            program = self._compile_filter_program(_DOF_SMOOTH_MASK_ALPHA_STRONG_FRAGMENT_SHADER)
        elif mode == "bicubic_mask":
            program = self._compile_filter_program(_BICUBIC_MASK_FRAGMENT_SHADER)
        elif mode == "bicubic_mask_alpha":
            program = self._compile_filter_program(_BICUBIC_MASK_ALPHA_FRAGMENT_SHADER)
        elif mode == "lanczos_mask":
            program = self._compile_filter_program(_LANCZOS_MASK_FRAGMENT_SHADER)
        elif mode == "lanczos_mask_alpha":
            program = self._compile_filter_program(_LANCZOS_MASK_ALPHA_FRAGMENT_SHADER)
        else:
            program = 0
        self._filter_programs[mode] = program
        if program:
            if mode.endswith("_mask") or mode.endswith("_mask_alpha"):
                self._filter_uniforms[mode] = {
                    "overlay": glGetUniformLocation(program, "u_overlay"),
                    "mask": glGetUniformLocation(program, "u_mask"),
                    "overlay_size": glGetUniformLocation(program, "u_overlaySize"),
                    "mask_size": glGetUniformLocation(program, "u_maskSize"),
                    "strength": glGetUniformLocation(program, "u_strength"),
                }
            else:
                self._filter_uniforms[mode] = {
                    "texture": glGetUniformLocation(program, "u_texture"),
                    "tex_size": glGetUniformLocation(program, "u_texSize"),
                    "strength": glGetUniformLocation(program, "u_strength"),
                }
        return program

    def _use_filter_program(
        self,
        mode: str,
        tex_width: int,
        tex_height: int,
        alpha_aware: bool = False,
    ) -> bool:
        program_mode = mode
        if alpha_aware and mode in ("bicubic", "lanczos"):
            program_mode = f"{mode}_alpha"
        program = self._get_filter_program(program_mode)
        if not program:
            return False
        glUseProgram(program)
        uniforms = self._filter_uniforms.get(program_mode, {})
        loc_tex = uniforms.get("texture", -1)
        if loc_tex is not None and loc_tex >= 0:
            glUniform1i(loc_tex, 0)
        loc_size = uniforms.get("tex_size", -1)
        if loc_size is not None and loc_size >= 0:
            width = max(1.0, float(tex_width))
            height = max(1.0, float(tex_height))
            glUniform2f(loc_size, width, height)
        loc_strength = uniforms.get("strength", -1)
        if loc_strength is not None and loc_strength >= 0:
            strength_value = (
                float(self.dof_alpha_smoothing_strength)
                if program_mode in {"dof_smooth_alpha", "dof_smooth_alpha_strong"}
                else float(self.texture_filter_strength)
            )
            glUniform1f(loc_strength, strength_value)
        return True

    def _use_filter_program_masked(
        self,
        mode: str,
        overlay_size: Tuple[int, int],
        mask_size: Tuple[int, int],
        alpha_aware: bool = False,
    ) -> bool:
        if mode == "dof_smooth_alpha":
            key = "dof_smooth_alpha_mask"
        elif mode == "dof_smooth_alpha_strong":
            key = "dof_smooth_alpha_strong_mask"
        else:
            key = f"{mode}_mask_alpha" if alpha_aware else f"{mode}_mask"
        program = self._get_filter_program(key)
        if not program:
            return False
        glUseProgram(program)
        uniforms = self._filter_uniforms.get(key, {})
        loc_overlay = uniforms.get("overlay", -1)
        if loc_overlay is not None and loc_overlay >= 0:
            glUniform1i(loc_overlay, 0)
        loc_mask = uniforms.get("mask", -1)
        if loc_mask is not None and loc_mask >= 0:
            glUniform1i(loc_mask, 1)
        loc_overlay_size = uniforms.get("overlay_size", -1)
        if loc_overlay_size is not None and loc_overlay_size >= 0:
            ow, oh = overlay_size
            glUniform2f(loc_overlay_size, max(1.0, float(ow)), max(1.0, float(oh)))
        loc_mask_size = uniforms.get("mask_size", -1)
        if loc_mask_size is not None and loc_mask_size >= 0:
            mw, mh = mask_size
            glUniform2f(loc_mask_size, max(1.0, float(mw)), max(1.0, float(mh)))
        loc_strength = uniforms.get("strength", -1)
        if loc_strength is not None and loc_strength >= 0:
            strength_value = (
                float(self.dof_alpha_smoothing_strength)
                if mode in {"dof_smooth_alpha", "dof_smooth_alpha_strong"}
                else float(self.texture_filter_strength)
            )
            glUniform1f(loc_strength, strength_value)
        return True

    def _stop_filter_program(self) -> None:
        glUseProgram(0)

    def _get_alpha_stencil_program(self) -> int:
        if self._alpha_stencil_program:
            return self._alpha_stencil_program
        program = self._compile_filter_program(_ALPHA_STENCIL_FRAGMENT_SHADER)
        self._alpha_stencil_program = int(program or 0)
        if self._alpha_stencil_program:
            self._alpha_stencil_uniform_texture = glGetUniformLocation(
                self._alpha_stencil_program, "u_texture"
            )
            self._alpha_stencil_uniform_threshold = glGetUniformLocation(
                self._alpha_stencil_program, "u_alphaThreshold"
            )
            self._alpha_stencil_uniform_softness = glGetUniformLocation(
                self._alpha_stencil_program, "u_alphaSoftness"
            )
            self._alpha_stencil_uniform_texel_size = glGetUniformLocation(
                self._alpha_stencil_program, "u_texelSize"
            )
            self._alpha_stencil_uniform_uv_min = glGetUniformLocation(
                self._alpha_stencil_program, "u_uvMin"
            )
            self._alpha_stencil_uniform_uv_max = glGetUniformLocation(
                self._alpha_stencil_program, "u_uvMax"
            )
        return self._alpha_stencil_program

    def _use_alpha_stencil_program(
        self,
        threshold: float,
        softness: float = 0.0,
        texel_size: Tuple[float, float] = (0.0, 0.0),
        uv_min: Tuple[float, float] = (0.0, 0.0),
        uv_max: Tuple[float, float] = (1.0, 1.0),
    ) -> bool:
        program = self._get_alpha_stencil_program()
        if not program:
            return False
        glUseProgram(program)
        if self._alpha_stencil_uniform_texture is not None and self._alpha_stencil_uniform_texture >= 0:
            glUniform1i(self._alpha_stencil_uniform_texture, 0)
        if self._alpha_stencil_uniform_threshold is not None and self._alpha_stencil_uniform_threshold >= 0:
            glUniform1f(self._alpha_stencil_uniform_threshold, float(threshold))
        if self._alpha_stencil_uniform_softness is not None and self._alpha_stencil_uniform_softness >= 0:
            glUniform1f(self._alpha_stencil_uniform_softness, float(max(0.0, softness)))
        if self._alpha_stencil_uniform_texel_size is not None and self._alpha_stencil_uniform_texel_size >= 0:
            glUniform2f(self._alpha_stencil_uniform_texel_size, float(texel_size[0]), float(texel_size[1]))
        if self._alpha_stencil_uniform_uv_min is not None and self._alpha_stencil_uniform_uv_min >= 0:
            glUniform2f(self._alpha_stencil_uniform_uv_min, float(uv_min[0]), float(uv_min[1]))
        if self._alpha_stencil_uniform_uv_max is not None and self._alpha_stencil_uniform_uv_max >= 0:
            glUniform2f(self._alpha_stencil_uniform_uv_max, float(uv_max[0]), float(uv_max[1]))
        return True

    def _get_mask_consumer_program(self) -> int:
        if self._mask_consumer_program:
            return self._mask_consumer_program
        program = self._compile_filter_program(_MASK_CONSUMER_FRAGMENT_SHADER)
        self._mask_consumer_program = int(program or 0)
        if self._mask_consumer_program:
            self._mask_consumer_uniform_texture = glGetUniformLocation(
                self._mask_consumer_program, "u_texture"
            )
            self._mask_consumer_uniform_mask = glGetUniformLocation(
                self._mask_consumer_program, "u_maskTexture"
            )
            self._mask_consumer_uniform_view_size = glGetUniformLocation(
                self._mask_consumer_program, "u_viewSize"
            )
        return self._mask_consumer_program

    def _ensure_mask_resources(self, width: int, height: int) -> bool:
        w = max(1, int(width))
        h = max(1, int(height))
        if (
            self._mask_fbo
            and self._mask_texture
            and self._mask_size == (w, h)
        ):
            return True
        self._destroy_mask_resources()
        try:
            tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, int(tex))
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(
                GL_TEXTURE_2D,
                0,
                GL_RGBA,
                w,
                h,
                0,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
                None,
            )
            fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, int(fbo))
            glFramebufferTexture2D(
                GL_FRAMEBUFFER,
                GL_COLOR_ATTACHMENT0,
                GL_TEXTURE_2D,
                int(tex),
                0,
            )
            complete = glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE
            glBindFramebuffer(GL_FRAMEBUFFER, 0)
            if not complete:
                try:
                    glDeleteFramebuffers(1, [int(fbo)])
                except Exception:
                    pass
                try:
                    glDeleteTextures(1, [int(tex)])
                except Exception:
                    pass
                return False
            self._mask_fbo = int(fbo)
            self._mask_texture = int(tex)
            self._mask_size = (w, h)
            return True
        except Exception:
            self._destroy_mask_resources()
            return False

    def _destroy_mask_resources(self) -> None:
        if self._mask_fbo:
            try:
                glDeleteFramebuffers(1, [int(self._mask_fbo)])
            except Exception:
                pass
        if self._mask_texture:
            try:
                glDeleteTextures(1, [int(self._mask_texture)])
            except Exception:
                pass
        self._mask_fbo = None
        self._mask_texture = None
        self._mask_size = (0, 0)

    def _current_viewport(self) -> Tuple[int, int, int, int]:
        hint_w = max(1, int(self.viewport_size_hint[0] if self.viewport_size_hint else 1))
        hint_h = max(1, int(self.viewport_size_hint[1] if self.viewport_size_hint else 1))
        # Prefer explicit renderer hints first; they are set by both live viewport
        # and export paths and avoid fragile GL_VIEWPORT queries on some drivers.
        if hint_w > 1 or hint_h > 1:
            return 0, 0, hint_w, hint_h
        try:
            vp = glGetIntegerv(GL_VIEWPORT)
            if vp is not None and len(vp) >= 4:
                w = int(vp[2])
                h = int(vp[3])
                if w > 0 and h > 0:
                    return int(vp[0]), int(vp[1]), w, h
        except Exception:
            pass
        return 0, 0, hint_w, hint_h

    def reset_layer_masks(self) -> None:
        """Clear any pending layer mask state before a new frame renders."""
        self.pending_mask_key = None
        self._mask_capture_mode = "none"
        self._mask_debug_draw_order = 0
        if self.mask_debug_enabled:
            self._mask_debug_frame_index += 1
            self._mask_debug_frame_active = False

    def _next_mask_debug_draw_order(self) -> int:
        self._mask_debug_draw_order += 1
        return self._mask_debug_draw_order

    def _mask_debug_matches_target(
        self,
        layer: LayerData,
        world_state: Dict,
        atlases: List[TextureAtlas],
    ) -> bool:
        target = (self.mask_debug_target or "").strip().lower()
        if not target:
            return True

        probes: List[str] = []
        probes.append((layer.name or "").lower())
        probes.append((getattr(layer, "shader_name", "") or "").lower())
        probes.append((world_state.get("sprite_name") or "").lower())

        for atlas in atlases or []:
            source_name = (getattr(atlas, "source_name", "") or "").lower()
            image_name = os.path.basename(getattr(atlas, "image_path", "") or "").lower()
            xml_name = os.path.basename(getattr(atlas, "xml_path", "") or "").lower()
            probes.extend([source_name, image_name, xml_name])

        return any(target in probe for probe in probes if probe)

    def _mask_debug_should_log_layer(
        self,
        layer: LayerData,
        world_state: Dict,
        atlases: List[TextureAtlas],
    ) -> bool:
        if not self.mask_debug_enabled:
            return False
        if self._mask_debug_frame_active:
            return True
        if self._mask_debug_matches_target(layer, world_state, atlases):
            self._mask_debug_frame_active = True
            return True
        return False

    def _log_mask_debug_layer(
        self,
        *,
        active: bool,
        draw_order: int,
        layer: LayerData,
        world_state: Dict,
        shader_name: Optional[str],
        effective_blend: int,
        mask_role: Optional[str],
        mask_key: Optional[str],
        stencil_write: bool,
        stencil_read: bool,
        stencil_read_attempted: bool,
    ) -> None:
        if not active:
            return

        layer_name = (layer.name or "").replace("'", "\\'")
        sprite_name = str(world_state.get("sprite_name") or "-").replace("'", "\\'")
        shader = (shader_name or "-").replace("'", "\\'")
        role = mask_role or "-"
        key = mask_key or "-"
        print(
            "[MaskDebug] "
            f"frame={self._mask_debug_frame_index} "
            f"time={self.current_time:.4f} "
            f"order={draw_order} "
            f"layer='{layer_name}' "
            f"id={layer.layer_id} "
            f"sprite='{sprite_name}' "
            f"shader='{shader}' "
            f"blend={effective_blend} "
            f"mask_role={role} "
            f"mask_key={key} "
            f"stencil_write={int(bool(stencil_write))} "
            f"stencil_read_attempted={int(bool(stencil_read_attempted))} "
            f"stencil_read_active={int(bool(stencil_read))}"
        )

    def _atlas_cache_key(self, atlas: Optional[TextureAtlas]) -> str:
        """Return a stable cache key for atlas-scoped lookups."""
        if not atlas:
            return "<none>"
        source = getattr(atlas, "source_name", None)
        if source:
            return str(source).strip().lower()
        image_path = getattr(atlas, "image_path", "") or ""
        if image_path:
            return os.path.basename(image_path).lower()
        return f"atlas_{id(atlas)}"

    def _sprite_alias_candidates(self, sprite_name: str) -> List[str]:
        """
        Generate fallback sprite names.
        Handles cases like JM_1st_hand_02.png -> JM_1st_hand.
        """
        base = (sprite_name or "").strip()
        if not base:
            return []
        seen: Set[str] = set()
        candidates: List[str] = []

        def _enqueue(value: str):
            value = value.strip()
            if not value or value in seen:
                return
            seen.add(value)
            candidates.append(value)

        _enqueue(base)
        if "." in base:
            _enqueue(base.rsplit(".", 1)[0])

        # Some atlas exports bake extensions into sprite names (e.g., JM_1st_hand.png).
        if "." not in base:
            for ext in (".png", ".avif", ".webp"):
                _enqueue(base + ext)

        numeric_working = base
        while True:
            reduced = re.sub(r"([ _-])\d+$", "", numeric_working)
            if reduced == numeric_working or not reduced:
                break
            _enqueue(reduced)
            numeric_working = reduced

        alpha_working = base
        while True:
            reduced = re.sub(r"([ _-])[A-Za-z]$", "", alpha_working)
            if reduced == alpha_working or not reduced:
                break
            _enqueue(reduced)
            alpha_working = reduced

        return [name for name in candidates if name != base]

    def _resolve_anchor_map_entry(
        self,
        anchor_map: Dict[str, Tuple[float, float]],
        sprite_name: str
    ) -> Optional[Tuple[float, float]]:
        """Return the best matching anchor override for a sprite name."""
        if not anchor_map or not sprite_name:
            return None
        if sprite_name in anchor_map:
            return anchor_map[sprite_name]
        for candidate in self._sprite_alias_candidates(sprite_name):
            if candidate in anchor_map:
                return anchor_map[candidate]
        sprite_lower = sprite_name.lower()
        for key, value in anchor_map.items():
            if isinstance(key, str) and key.lower() == sprite_lower:
                return value
        return None

    def _compute_sprite_alias(
        self,
        sprite_name: str,
        atlas: Optional[TextureAtlas]
    ) -> Tuple[Optional[str], Optional[SpriteInfo]]:
        """Return (alias_name, sprite) if a fallback sprite exists."""
        if not atlas:
            return None, None
        for candidate in self._sprite_alias_candidates(sprite_name):
            sprite = atlas.get_sprite(candidate)
            if sprite:
                return candidate, sprite
        return None, None

    def _get_sprite_from_atlas(
        self,
        sprite_name: str,
        atlas: Optional[TextureAtlas],
        allow_alias: bool = True
    ) -> Tuple[Optional[SpriteInfo], str]:
        """Lookup sprite in atlas, falling back to aliases that strip suffixes."""
        if not sprite_name or not atlas:
            return None, sprite_name

        atlas_key = self._atlas_cache_key(atlas)
        cache_key = (atlas_key, sprite_name)
        cached = self._sprite_alias_cache.get(cache_key)
        if cached is not None:
            if cached:
                sprite = atlas.get_sprite(cached)
                if sprite:
                    return sprite, cached
            else:
                return None, sprite_name

        sprite = atlas.get_sprite(sprite_name)
        if sprite:
            self._sprite_alias_cache[cache_key] = sprite_name
            return sprite, sprite_name

        if not allow_alias:
            self._sprite_alias_cache[cache_key] = ""
            return None, sprite_name

        alias_name, alias_sprite = self._compute_sprite_alias(sprite_name, atlas)
        if alias_name and alias_sprite:
            self._sprite_alias_cache[cache_key] = alias_name
            warn_key = (sprite_name.lower(), atlas_key)
            if warn_key not in self._sprite_alias_warnings:
                sheet_label = getattr(atlas, "source_name", None) or os.path.basename(
                    getattr(atlas, "image_path", "") or ""
                ) or "atlas"
                print(f"[SpriteAlias] '{sprite_name}' missing in {sheet_label}; using '{alias_name}'.")
                self._sprite_alias_warnings.add(warn_key)
            return alias_sprite, alias_name

        self._sprite_alias_cache[cache_key] = ""
        return None, sprite_name

    def _find_sprite_in_atlases(
        self,
        sprite_name: str,
        atlases: Optional[List[TextureAtlas]],
        allow_alias: bool = True
    ) -> Tuple[Optional[SpriteInfo], Optional[TextureAtlas], str]:
        """Search all atlases in order, returning the first match (with alias support)."""
        if not sprite_name or not atlases:
            return None, None, sprite_name
        for atlas in atlases:
            sprite, resolved_name = self._get_sprite_from_atlas(sprite_name, atlas, allow_alias)
            if sprite:
                return sprite, atlas, resolved_name
        return None, None, sprite_name

    def _get_hires_scale(self, atlas: TextureAtlas) -> float:
        """Return the scale applied to hi-res atlases."""
        return 0.5 if atlas.is_hires else 1.0

    def _build_polygon_local_vertices(
        self, sprite: SpriteInfo, atlas: TextureAtlas
    ) -> List[Tuple[float, float]]:
        """Return polygon vertices in local sprite space, scaled appropriately."""
        if not sprite.vertices:
            return []
        scale = self._get_hires_scale(atlas) * self.position_scale
        trim_shift = self.trim_shift_multiplier
        offset_dx = sprite.offset_x * trim_shift - sprite.offset_x
        offset_dy = sprite.offset_y * trim_shift - sprite.offset_y
        pivot_dx = 0.0
        pivot_dy = 0.0
        if getattr(atlas, "pivot_mode", None) == "dof":
            # DOF mesh vertices are already pivot-local; do not apply pivot shifts.
            pivot_dx = 0.0
            pivot_dy = 0.0
        return [
            ((vx + offset_dx + pivot_dx) * scale, (vy + offset_dy + pivot_dy) * scale)
            for vx, vy in sprite.vertices
        ]

    @staticmethod
    def _compute_dof_sprite_anchor(sprite: SpriteInfo) -> Optional[Tuple[float, float]]:
        """Return the pivot anchor for a DOF sprite in sprite-local pixels."""
        try:
            pivot_x = float(sprite.pivot_x)
            pivot_y = float(sprite.pivot_y)
        except (TypeError, ValueError):
            return None
        rect_w = sprite.original_w if sprite.original_w > 0 else sprite.w
        rect_h = sprite.original_h if sprite.original_h > 0 else sprite.h
        if rect_w <= 0 or rect_h <= 0:
            return None
        anchor_x = rect_w * pivot_x - sprite.offset_x
        anchor_y = rect_h * pivot_y - sprite.offset_y
        return anchor_x, anchor_y

    def _build_quad_local_vertices(
        self, sprite: SpriteInfo, atlas: TextureAtlas
    ) -> List[Tuple[float, float]]:
        """Return quad vertices for sprites without polygon meshes."""
        scale = self._get_hires_scale(atlas) * self.position_scale
        trim_shift = self.trim_shift_multiplier
        if getattr(atlas, "pivot_mode", None) == "dof":
            base_x = 0.0
            base_y = 0.0
        else:
            base_x = sprite.offset_x * trim_shift
            base_y = sprite.offset_y * trim_shift

        if sprite.rotated:
            width = sprite.h
            height = sprite.w
        else:
            width = sprite.w
            height = sprite.h

        corners = [
            (base_x, base_y),
            (base_x + width, base_y),
            (base_x + width, base_y + height),
            (base_x, base_y + height),
        ]
        return [(x * scale, y * scale) for x, y in corners]

    def compute_local_vertices(
        self, sprite: SpriteInfo, atlas: TextureAtlas
    ) -> List[Tuple[float, float]]:
        """
        Return the sprite's local-space vertices scaled with hi-res and position factors.
        Polygon meshes return their authored vertices; quads return the rectangle corners.
        """
        if sprite.has_polygon_mesh:
            return self._build_polygon_local_vertices(sprite, atlas)
        return self._build_quad_local_vertices(sprite, atlas)

    def _build_polygon_geometry(
        self, sprite: SpriteInfo, atlas: TextureAtlas
    ) -> Optional[Tuple[List[Tuple[float, float]], List[Tuple[float, float]], List[int]]]:
        """Return (vertices, texcoords, triangles) for polygon sprites if available."""
        if not sprite.has_polygon_mesh:
            return None
        vertices = self._build_polygon_local_vertices(sprite, atlas)
        if len(vertices) != len(sprite.vertices_uv) or len(sprite.triangles) < 3:
            return None
        return vertices, sprite.vertices_uv, sprite.triangles

    @staticmethod
    def _point_in_triangle(px: float, py: float, v0: Tuple[float, float],
                           v1: Tuple[float, float], v2: Tuple[float, float]) -> bool:
        """Return True if point lies inside the triangle defined by v0/v1/v2."""
        x0, y0 = v0
        x1, y1 = v1
        x2, y2 = v2
        denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(denom) < 1e-8:
            return False
        a = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / denom
        b = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / denom
        c = 1.0 - a - b
        epsilon = -1e-5
        return (a >= epsilon) and (b >= epsilon) and (c >= epsilon)
    
    def calculate_world_state(
        self,
        layer: LayerData,
        time: float,
        player: AnimationPlayer,
        layer_map: Dict[int, LayerData],
        world_states: Dict[int, Dict],
        atlases: List[TextureAtlas] = None,
        layer_atlas_overrides: Dict[int, List[TextureAtlas]] = None,
        pivot_remap_context: Optional[Dict[int, bool]] = None,
        recursion_guard: Optional[Set[int]] = None
    ) -> Dict:
        """
        Calculate the world-space state for a layer using 2x3 affine matrices
        
        CRITICAL INSIGHT FROM MSM GAME ENGINE:
        The game uses a standard 2D affine transformation system where:
        1. Each layer has an anchor point (pivot) that defines the rotation/scale center
        2. The position in keyframes is the position of the layer's anchor point
        3. Negative scale values are kept IN the matrix - they naturally cause mirroring
        
        The transformation order is: Translate to position -> Rotate -> Scale
        The anchor point offsets the sprite so rotation happens around the anchor.
        
        Matrix format:
        | m00  m01  tx |
        | m10  m11  ty |
        
        Child transform = Parent_Matrix × Child_Local_Matrix
        
        Args:
            layer: Layer to calculate state for
            time: Current animation time
            player: Animation player for interpolation
            layer_map: Map of layer IDs to layers
            world_states: Cache of already-calculated world states
        
        Returns:
            Dictionary containing world-space transform matrix and other properties
        """
        if recursion_guard is None:
            recursion_guard = set()
        recursion_guard.add(layer.layer_id)

        def _build_state() -> Dict:
            local_state = player.get_layer_state(layer, time, include_global=False)

            tint_r = tint_g = tint_b = tint_a = 1.0
            resolved_color = self._resolve_layer_color(layer, time)
            if resolved_color:
                try:
                    tint_r, tint_g, tint_b, tint_a = resolved_color
                except (TypeError, ValueError):
                    tint_r = tint_g = tint_b = tint_a = 1.0
            if tint_r != 1.0 or tint_g != 1.0 or tint_b != 1.0:
                local_state['r'] = int(max(0, min(255, round(local_state['r'] * tint_r))))
                local_state['g'] = int(max(0, min(255, round(local_state['g'] * tint_g))))
                local_state['b'] = int(max(0, min(255, round(local_state['b'] * tint_b))))

            local_alpha = max(0.0, min(1.0, float(local_state.get('a', 255)) / 255.0))
            
            # Extract local transform values
            # Apply base_world_scale to convert JSON coordinates to screen space
            pos_multiplier = self.local_position_multiplier
            pos_x = local_state['pos_x'] * pos_multiplier * self.base_world_scale * self.position_scale
            pos_y = local_state['pos_y'] * pos_multiplier * self.base_world_scale * self.position_scale
            rotation = local_state['rotation'] + self.rotation_bias
            scale_x = (local_state['scale_x'] / 100.0) * self.scale_bias_x
            scale_y = (local_state['scale_y'] / 100.0) * self.scale_bias_y
            opacity = (local_state['opacity'] / 100.0) * local_alpha * tint_a
            opacity = max(0.0, min(1.0, opacity))
            if player.animation and getattr(player.animation, "dof_anim_flip_y", False):
                pos_y = -pos_y
                rotation = -rotation
            
            # CRITICAL FIX: Keep negative scale values in the matrix!
            # The game engine does NOT separate flipping from the matrix transformation.
            # Negative scale naturally causes mirroring through the matrix itself.
            # This ensures the anchor point offset is calculated correctly.
            # DO NOT use abs() on scale values!
            
            # Layer anchor values - allow per-layer overrides for interactive editing
            if self.anchor_overrides and layer.layer_id in self.anchor_overrides:
                anchor_local_x, anchor_local_y = self.anchor_overrides[layer.layer_id]
            else:
                anchor_local_x = layer.anchor_x
                anchor_local_y = layer.anchor_y
            if self.anchor_scale_x != 1.0:
                anchor_local_x *= self.anchor_scale_x
            if self.anchor_scale_y != 1.0:
                anchor_local_y *= self.anchor_scale_y
            if self.anchor_flip_x:
                anchor_local_x = -anchor_local_x
            if self.anchor_flip_y:
                anchor_local_y = -anchor_local_y
            original_anchor_x = anchor_local_x
            original_anchor_y = anchor_local_y
            pivot_log: Optional[Dict[str, Any]] = None
            sprite_anchor_override: Optional[Tuple[float, float]] = None
            has_costume_pivot_context = bool(
                pivot_remap_context and pivot_remap_context.get(layer.layer_id)
            )
            anchor_map = getattr(layer, "sprite_anchor_map", None)
            use_anchor_map = False
            anchor_map_missing = False

            # If anchors are effectively zero (pivot-baked mesh sprites), apply anchor scale
            # to the layer position so the anchor spacing sliders still affect the viewport.
            if not anchor_map and abs(anchor_local_x) < 1e-6 and abs(anchor_local_y) < 1e-6:
                if self.anchor_scale_x != 1.0:
                    pos_x *= self.anchor_scale_x
                if self.anchor_scale_y != 1.0:
                    pos_y *= self.anchor_scale_y
                if self.anchor_flip_x:
                    pos_x = -pos_x
                if self.anchor_flip_y:
                    pos_y = -pos_y
    
            # COSTUME SPRITE PIVOT ADJUSTMENT:
            # From FunctionsAll.txt RemapLayer function, when a costume sprite has a non-default
            # pivot (not 0.5, 0.5), the game adjusts the anchor:
            #   if ((pivot_x != 0.5) || (pivot_y != 0.5)) {
            #       setHAnchor(sprite_width * pivot_x - layer_anchor_x);
            #       setVAnchor(sprite_height * pivot_y - layer_anchor_y);
            #   }
            #
            # This means the NEW anchor = sprite_size * pivot - original_anchor
            # We need to apply this adjustment when rendering costume sprites with custom pivots.
            sprite_name = local_state['sprite_name']
            if sprite_name:
                mapped_anchor = None
                dof_sprite: Optional[SpriteInfo] = None
                dof_atlas: Optional[TextureAtlas] = None
                if atlases:
                    sprite_check, atlas_check, _ = self._find_sprite_in_atlases(sprite_name, atlases)
                    if sprite_check and atlas_check and getattr(atlas_check, "pivot_mode", None) == "dof":
                        dof_sprite = sprite_check
                        dof_atlas = atlas_check
                if isinstance(anchor_map, dict):
                    mapped_anchor = self._resolve_anchor_map_entry(anchor_map, sprite_name)
                    if mapped_anchor is None and anchor_map:
                        anchor_map_missing = True
                if mapped_anchor is None and dof_sprite and dof_atlas:
                    computed_anchor = self._compute_dof_sprite_anchor(dof_sprite)
                    if computed_anchor is not None:
                        mapped_anchor = computed_anchor
                        anchor_map_missing = False
                if mapped_anchor and len(mapped_anchor) >= 2:
                    mapped_anchor_x = float(mapped_anchor[0])
                    mapped_anchor_y = float(mapped_anchor[1])
                    if self.anchor_scale_x != 1.0:
                        mapped_anchor_x *= self.anchor_scale_x
                    if self.anchor_scale_y != 1.0:
                        mapped_anchor_y *= self.anchor_scale_y
                    if self.anchor_flip_x:
                        mapped_anchor_x = -mapped_anchor_x
                    if self.anchor_flip_y:
                        mapped_anchor_y = -mapped_anchor_y
                    # NOTE: Do NOT mirror mapped anchors for negative scale.
                    # The layer matrix already contains the negative scale,
                    # which mirrors the sprite about the layer anchor. Flipping
                    # the anchor again double-mirrors and shifts duplicated
                    # sprites (e.g., right pupil/wing) out of their bounds.
                    sprite_anchor_override = (
                        original_anchor_x - mapped_anchor_x,
                        original_anchor_y - mapped_anchor_y,
                    )
                    pivot_log = {
                        "sprite": sprite_name,
                        "mapped_anchor": tuple(mapped_anchor),
                        "mapped_anchor_effective": (mapped_anchor_x, mapped_anchor_y),
                        "original_anchor": (original_anchor_x, original_anchor_y),
                        "anchor_offset": sprite_anchor_override,
                    }
                    use_anchor_map = True
                if not use_anchor_map:
                    # First check costume atlas overrides, then fall back to all atlases
                    search_atlases: List[TextureAtlas] = []
                    if layer_atlas_overrides and layer.layer_id in layer_atlas_overrides:
                        search_atlases = layer_atlas_overrides[layer.layer_id]
                    elif atlases:
                        search_atlases = atlases
                    found_sprite, search_atlas, resolved_sprite = self._find_sprite_in_atlases(
                        sprite_name,
                        search_atlases
                    )
                    if found_sprite and search_atlas:
                        sprite = found_sprite
                        # Check if pivot is non-default (not 0.5, 0.5)
                        if abs(sprite.pivot_x - 0.5) > 0.001 or abs(sprite.pivot_y - 0.5) > 0.001:
                            # DOF sprites already encode pivot-local geometry; do not apply costume pivot adjustment.
                            if getattr(search_atlas, "pivot_mode", None) == "dof":
                                pass
                            elif self.costume_pivot_adjustment_enabled and not has_costume_pivot_context:
                                # Costume logic only runs when both sheet + sprite remaps were supplied.
                                pass
                            else:
                                # Get sprite dimensions (use original dimensions, scaled for hires)
                                sprite_w = sprite.original_w if sprite.original_w > 0 else sprite.w
                                sprite_h = sprite.original_h if sprite.original_h > 0 else sprite.h
                                if sprite_w <= 0 and sprite.w > 0:
                                    sprite_w = sprite.w
                                if sprite_h <= 0 and sprite.h > 0:
                                    sprite_h = sprite.h
                                hires_scale = self._get_hires_scale(search_atlas)
                                sprite_w *= hires_scale
                                sprite_h *= hires_scale

                                # COSTUME SPRITE PIVOT FIX:
                                # Formula: new_anchor = sprite_size * pivot - old_anchor
                                if self.costume_pivot_adjustment_enabled:
                                    adjusted_anchor_x = sprite_w * sprite.pivot_x - original_anchor_x
                                    adjusted_anchor_y = sprite_h * sprite.pivot_y - original_anchor_y
                                else:
                                    adjusted_anchor_x = sprite_w * sprite.pivot_x
                                    adjusted_anchor_y = sprite_h * sprite.pivot_y
                                sprite_anchor_override = (
                                    original_anchor_x - adjusted_anchor_x,
                                    original_anchor_y - adjusted_anchor_y,
                                )
                                pivot_log = {
                                    "sprite": sprite_name,
                                    "resolved_sprite": resolved_sprite,
                                    "pivot": (sprite.pivot_x, sprite.pivot_y),
                                    "sprite_size": (sprite_w, sprite_h),
                                    "original_anchor": (original_anchor_x, original_anchor_y),
                                    "adjusted_anchor": (adjusted_anchor_x, adjusted_anchor_y),
                                    "anchor_offset": sprite_anchor_override,
                                }
                if anchor_map_missing and sprite_name:
                    warn_key = (layer.name or "", sprite_name)
                    if warn_key not in self._anchor_map_warnings:
                        print(
                            f"[AnchorMap] Missing pivot entry for sprite '{sprite_name}' on layer '{layer.name}'."
                        )
                        self._anchor_map_warnings.add(warn_key)
    
            anchor_x = (anchor_local_x + self.anchor_bias_x) * self.base_world_scale * self.position_scale
            anchor_y = (anchor_local_y + self.anchor_bias_y) * self.base_world_scale * self.position_scale
            
            # Log if enabled
            if self.enable_logging:
                log_entry: Dict[str, Any] = {
                    'layer': layer.name,
                    'layer_id': layer.layer_id,
                    'original_anchor': (original_anchor_x, original_anchor_y),
                    'layer_anchor': (anchor_local_x, anchor_local_y),
                    'position': (pos_x, pos_y),
                    'rotation': rotation,
                    'scale': (scale_x, scale_y)
                }
                if pivot_log:
                    log_entry['pivot_adjustment'] = pivot_log
                    log_entry['sprite_anchor_offset'] = sprite_anchor_override
                if anchor_map_missing and sprite_name:
                    log_entry['anchor_map_missing'] = sprite_name
                self.log_data.append(log_entry)
            
            rot_rad = math.radians(rotation)
            cos_r = math.cos(rot_rad)
            sin_r = math.sin(rot_rad)
            
            # Build the transformation matrix
            # The standard 2D affine transform for rotation around a pivot point is:
            # T(pos) * R(angle) * S(scale) * T(-anchor)
            #
            # This means:
            # 1. First translate by -anchor (move anchor to origin)
            # 2. Then scale (including negative scale for mirroring!)
            # 3. Then rotate
            # 4. Finally translate to position
            #
            # The combined matrix is computed by multiplying these together
            
            # Build the 2x3 matrix components (rotation * scale)
            # Standard 2D rotation-scale matrix:
            # | cos*sx  -sin*sy |
            # | sin*sx   cos*sy |
            m00 = scale_x * cos_r
            m01 = -scale_y * sin_r
            m10 = scale_x * sin_r
            m11 = scale_y * cos_r
            
            # The translation combines position and the transformed anchor offset
            # T(pos) * R * S * T(-anchor) gives us:
            # tx = pos_x + (R*S * (-anchor)).x = pos_x - (m00*anchor_x + m01*anchor_y)
            # ty = pos_y + (R*S * (-anchor)).y = pos_y - (m10*anchor_x + m11*anchor_y)
            tx = pos_x - (m00 * anchor_x + m01 * anchor_y)
            ty = pos_y - (m10 * anchor_x + m11 * anchor_y)
            
            # Track anchor position in world space (before parent)
            child_anchor_world_x = pos_x
            child_anchor_world_y = pos_y
    
            # If this layer has a parent, multiply by parent's matrix
            child_m00 = m00
            child_m01 = m01
            child_m10 = m10
            child_m11 = m11
            child_tx = tx
            child_ty = ty
    
            parent_mix = max(0.0, min(1.0, self.parent_mix))
    
            world_anchor_x = child_anchor_world_x
            world_anchor_y = child_anchor_world_y
    
            parent_state: Optional[Dict] = None
            if layer.parent_id >= 0 and layer.parent_id in layer_map:
                parent_layer = layer_map[layer.parent_id]
    
                if layer.parent_id in recursion_guard:
                    self._log_cycle_warning(layer, parent_layer)
                else:
                    if layer.parent_id in world_states:
                        parent_state = world_states[layer.parent_id]
                    else:
                        parent_state = self.calculate_world_state(
                            parent_layer,
                            time,
                            player,
                            layer_map,
                            world_states,
                            atlases,
                            layer_atlas_overrides,
                            pivot_remap_context,
                            recursion_guard,
                        )
                        world_states[layer.parent_id] = parent_state
    
            if parent_state is not None:
                p_m00 = parent_state['m00']
                p_m01 = parent_state['m01']
                p_m10 = parent_state['m10']
                p_m11 = parent_state['m11']
                p_tx = parent_state['tx']
                p_ty = parent_state['ty']
    
                world_m00 = p_m00 * m00 + p_m01 * m10
                world_m01 = p_m00 * m01 + p_m01 * m11
                world_m10 = p_m10 * m00 + p_m11 * m10
                world_m11 = p_m10 * m01 + p_m11 * m11
                world_tx = p_m00 * tx + p_m01 * ty + p_tx
                world_ty = p_m10 * tx + p_m11 * ty + p_ty
                world_anchor_x = p_m00 * child_anchor_world_x + p_m01 * child_anchor_world_y + p_tx
                world_anchor_y = p_m10 * child_anchor_world_x + p_m11 * child_anchor_world_y + p_ty
                
                # NOTE: We do NOT multiply opacity from parent layers!
                # In MSM's animation system, each layer has its own independent opacity.
                # This allows child layers to be visible even when parent layers have opacity 0.
                # The parent layer with opacity 0 acts as a "null" or "control" layer that
                # only provides transform information without affecting child visibility.
                # This is how RARE/EPIC variants work - they hide the normal sprite layer
                # (opacity 0) but show the variant sprite layer (opacity 100) as a child.
            else:
                world_m00 = m00
                world_m01 = m01
                world_m10 = m10
                world_m11 = m11
                world_tx = tx
                world_ty = ty
    
            if parent_mix < 1.0:
                mix_child = 1.0 - parent_mix
                world_m00 = world_m00 * parent_mix + child_m00 * mix_child
                world_m01 = world_m01 * parent_mix + child_m01 * mix_child
                world_m10 = world_m10 * parent_mix + child_m10 * mix_child
                world_m11 = world_m11 * parent_mix + child_m11 * mix_child
                world_tx = world_tx * parent_mix + child_tx * mix_child
                world_ty = world_ty * parent_mix + child_ty * mix_child
                world_anchor_x = world_anchor_x * parent_mix + child_anchor_world_x * mix_child
                world_anchor_y = world_anchor_y * parent_mix + child_anchor_world_y * mix_child
    
            world_tx += self.world_offset_x
            world_ty += self.world_offset_y
            world_anchor_x += self.world_offset_x
            world_anchor_y += self.world_offset_y

            result = {
                # 2x3 transformation matrix
                'm00': world_m00,
                'm01': world_m01,
                'm10': world_m10,
                'm11': world_m11,
                'tx': world_tx,
                'ty': world_ty,
                'anchor_world_x': world_anchor_x,
                'anchor_world_y': world_anchor_y,
                # Other properties
                'world_opacity': opacity,
                'sprite_name': local_state['sprite_name'],
                'r': local_state['r'],
                'g': local_state['g'],
                'b': local_state['b'],
                'a': local_state.get('a', 255),
                'opacity_raw': float(local_state.get('opacity', 100.0)),
                'local_alpha': local_alpha,
                'tint_alpha': float(tint_a),
                'depth': local_state.get('depth', 0.0),
                'shader': getattr(layer, 'shader_name', None),
                'sprite_anchor_offset': sprite_anchor_override,
                'anchor_map_missing': anchor_map_missing,
                # Debug info
                'rotation': rotation,
                'scale_x': scale_x,
                'scale_y': scale_y,
            }
            return result

        try:
            return _build_state()
        finally:
            recursion_guard.discard(layer.layer_id)

    def _resolve_layer_color(
        self,
        layer: LayerData,
        time: float
    ) -> Optional[Tuple[float, float, float, float]]:
        """Return the active tint, honoring gradients and animated overrides."""
        try:
            animator = getattr(layer, "color_animator", None)
            gradient = getattr(layer, "color_gradient", None)
            base_color = getattr(layer, "color_tint", None)

            if animator:
                animated = self._evaluate_color_animation(animator, time)
                if animated is not None:
                    return animated
            if gradient:
                ramp = self._evaluate_color_gradient(gradient, time)
                if ramp is not None:
                    return ramp
            return base_color
        except Exception as exc:  # pragma: no cover - diagnostics
            if self.enable_logging:
                self.log_data.append(f"Color resolve failed for {layer.name}: {exc}")
            return getattr(layer, "color_tint", None)

    def _evaluate_color_gradient(
        self,
        gradient: Dict[str, Any],
        time: float
    ) -> Optional[Tuple[float, float, float, float]]:
        stops = (gradient or {}).get("stops") or []
        if not stops:
            return None
        if len(stops) == 1:
            return tuple(stops[0].get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        period = gradient.get("period")
        if not period or period <= 0:
            period = gradient.get("duration") or self.animation_duration
        if not period or period <= 0:
            period = 1.0

        offset = gradient.get("offset", 0.0)
        speed = gradient.get("speed", 1.0)
        mode = gradient.get("mode") or ("pingpong" if gradient.get("ping_pong") else "loop")
        local_time = (time - offset) * speed

        if mode == "once" or gradient.get("loop") is False:
            normalized = max(0.0, min(1.0, local_time / period))
        elif mode == "pingpong":
            phase = (local_time / period) % 2.0
            normalized = 1.0 - abs(phase - 1.0)
        else:
            normalized = (local_time / period) % 1.0

        prev_stop = stops[0]
        next_stop = None
        for stop in stops:
            position = stop.get("position", 0.0)
            if position <= normalized:
                prev_stop = stop
            elif position > normalized:
                next_stop = stop
                break

        if next_stop is None:
            return tuple(prev_stop.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        span = next_stop.get("position", 0.0) - prev_stop.get("position", 0.0)
        if span <= 0:
            return tuple(next_stop.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        ratio = (normalized - prev_stop.get("position", 0.0)) / span
        interpolation = (prev_stop.get("interpolation") or next_stop.get("interpolation") or "").lower()
        if interpolation == "hold":
            return tuple(prev_stop.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]
        return self._lerp_color(
            tuple(prev_stop.get("color") or (1.0, 1.0, 1.0, 1.0)),
            tuple(next_stop.get("color") or (1.0, 1.0, 1.0, 1.0)),
            max(0.0, min(1.0, ratio))
        )

    def _evaluate_color_animation(
        self,
        animator: Dict[str, Any],
        time: float
    ) -> Optional[Tuple[float, float, float, float]]:
        keyframes = (animator or {}).get("keyframes") or []
        if not keyframes:
            return None
        if len(keyframes) == 1:
            return tuple(keyframes[0].get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        duration = animator.get("duration")
        if not duration or duration <= 0:
            duration = keyframes[-1].get("time") or self.animation_duration
        if not duration or duration <= 0:
            duration = 1.0

        offset = animator.get("offset", 0.0)
        speed = animator.get("speed", 1.0)
        local_time = (time - offset) * speed

        if animator.get("loop", True):
            local_time = local_time % duration
        else:
            local_time = max(0.0, min(duration, local_time))

        prev_kf = keyframes[0]
        next_kf = None
        for frame in keyframes:
            key_time = frame.get("time", 0.0)
            if key_time <= local_time:
                prev_kf = frame
            elif key_time > local_time:
                next_kf = frame
                break

        if next_kf is None:
            return tuple(prev_kf.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        span = next_kf.get("time", 0.0) - prev_kf.get("time", 0.0)
        if span <= 0:
            return tuple(next_kf.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]

        ratio = (local_time - prev_kf.get("time", 0.0)) / span
        interpolation = (prev_kf.get("interpolation") or next_kf.get("interpolation") or "").lower()
        if interpolation == "hold":
            return tuple(prev_kf.get("color") or (1.0, 1.0, 1.0, 1.0))  # type: ignore[return-value]
        return self._lerp_color(
            tuple(prev_kf.get("color") or (1.0, 1.0, 1.0, 1.0)),
            tuple(next_kf.get("color") or (1.0, 1.0, 1.0, 1.0)),
            max(0.0, min(1.0, ratio))
        )

    @staticmethod
    def _lerp_color(
        a: Tuple[float, float, float, float],
        b: Tuple[float, float, float, float],
        t: float
    ) -> Tuple[float, float, float, float]:
        """Linear interpolation between two RGBA tuples."""
        return tuple(ai + (bi - ai) * t for ai, bi in zip(a, b))
    
    def render_layer(
        self,
        layer: LayerData,
        world_state: Dict,
        atlases: List[TextureAtlas],
        layer_offsets: Dict[int, Tuple[float, float]]
    ):
        """
        Render a single layer using its 2x3 transformation matrix
        
        Args:
            layer: Layer to render
            world_state: Pre-calculated world state with 2x3 matrix
            atlases: List of texture atlases to search for sprites
            layer_offsets: User-applied offsets for interactive dragging
        """
        draw_order = self._next_mask_debug_draw_order()
        mask_debug_active = self._mask_debug_should_log_layer(layer, world_state, atlases)

        if self.fast_preview_enabled:
            shader_name = self._resolve_effective_shader_name(world_state, atlases)
            shader_preset = self._get_shader_preset(shader_name)
            blend_override = self._blend_value_from_preset(shader_preset)
            effective_blend = self._resolve_effective_layer_blend(layer.blend_mode, blend_override)
            premultiply_color = effective_blend != BlendMode.STRAIGHT_ALPHA
            set_blend_mode(effective_blend)
            glPushMatrix()
            offset_x, offset_y = layer_offsets.get(layer.layer_id, (0, 0))
            if offset_x != 0 or offset_y != 0:
                glTranslatef(offset_x, offset_y, 0)
            matrix = [
                world_state['m00'], world_state['m10'], 0.0, 0.0,
                world_state['m01'], world_state['m11'], 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                world_state['tx'], world_state['ty'], 0.0, 1.0
            ]
            glMultMatrixf(matrix)
            self.render_sprite(
                world_state,
                world_state['world_opacity'],
                atlases,
                layer,
                render=True,
                shader_behavior=None,
                shader_name_override=shader_name,
                premultiply_color=premultiply_color,
            )
            glPopMatrix()
            reset_blend_mode()
            self._log_mask_debug_layer(
                active=mask_debug_active,
                draw_order=draw_order,
                layer=layer,
                world_state=world_state,
                shader_name=shader_name,
                effective_blend=effective_blend,
                mask_role=getattr(layer, "mask_role", None),
                mask_key=getattr(layer, "mask_key", None),
                stencil_write=False,
                stencil_read=False,
                stencil_read_attempted=False,
            )
            return

        mask_role = getattr(layer, "mask_role", None)
        mask_key = getattr(layer, "mask_key", None)
        shader_name = self._resolve_effective_shader_name(world_state, atlases)
        shader_preset = self._get_shader_preset(shader_name)
        shader_behavior = self._get_shader_behavior(shader_name)
        blend_override = self._blend_value_from_preset(shader_preset)
        effective_blend = self._resolve_effective_layer_blend(layer.blend_mode, blend_override)

        if mask_role in {"mask_source", "mask_source_visible"} and mask_key:
            mask_written = self._render_mask_source_layer(
                layer,
                world_state,
                atlases,
                layer_offsets,
                mask_key,
                render_visible=(mask_role == "mask_source_visible"),
            )
            self._log_mask_debug_layer(
                active=mask_debug_active,
                draw_order=draw_order,
                layer=layer,
                world_state=world_state,
                shader_name=shader_name,
                effective_blend=effective_blend,
                mask_role=mask_role,
                mask_key=mask_key,
                stencil_write=mask_written,
                stencil_read=False,
                stencil_read_attempted=False,
            )
            return

        mask_consumer_mode = "none"
        stencil_read_attempted = bool(mask_role == "mask_consumer" and mask_key)
        if mask_role == "mask_consumer" and mask_key:
            mask_consumer_mode = self._activate_mask_consumer(mask_key)

        # Apply blend mode for this layer
        # The blend mode determines how this layer's pixels combine with the background
        # Common modes:
        # - 0: Normal (premultiplied alpha)
        # - 1: Additive (for glows - adds light without darkening)
        premultiply_color = effective_blend != BlendMode.STRAIGHT_ALPHA
        set_blend_mode(effective_blend)
        
        glPushMatrix()
        
        # Apply user offset first (in world space)
        offset_x, offset_y = layer_offsets.get(layer.layer_id, (0, 0))
        if offset_x != 0 or offset_y != 0:
            glTranslatef(offset_x, offset_y, 0)
        
        # Apply the 2x3 transformation matrix using OpenGL
        # Convert 2x3 to 4x4 for OpenGL (add identity Z and W)
        # OpenGL uses column-major order, so we need to transpose
        # | m00  m01   0   tx |
        # | m10  m11   0   ty |
        # |  0    0    1    0 |
        # |  0    0    0    1 |
        matrix = [
            world_state['m00'], world_state['m10'], 0.0, 0.0,  # Column 1
            world_state['m01'], world_state['m11'], 0.0, 0.0,  # Column 2
            0.0, 0.0, 1.0, 0.0,                                 # Column 3
            world_state['tx'], world_state['ty'], 0.0, 1.0     # Column 4
        ]
        glMultMatrixf(matrix)
        
        # Render sprite - NO texture flipping needed, matrix handles mirroring!
        if mask_consumer_mode == "alpha":
            draw_info = self.render_sprite(
                world_state,
                world_state['world_opacity'],
                atlases,
                layer,
                render=False,
                shader_behavior=shader_behavior,
                shader_name_override=shader_name,
                premultiply_color=premultiply_color,
            )
            if draw_info:
                self._render_sprite_with_alpha_mask(draw_info)
        else:
            draw_info = self.render_sprite(
                world_state,
                world_state['world_opacity'],
                atlases,
                layer,
                render=not (shader_behavior and shader_behavior.replace_base_sprite),
                shader_behavior=shader_behavior,
                shader_name_override=shader_name,
                premultiply_color=premultiply_color,
            )
        if shader_behavior and draw_info:
            self._render_shader_overlay(draw_info, shader_behavior, shader_preset)
        
        glPopMatrix()
        self._deactivate_mask_consumer(mask_consumer_mode)
        
        # Reset blend mode to default after rendering this layer
        # This ensures subsequent layers start with the correct blend state
        reset_blend_mode()
        self._log_mask_debug_layer(
            active=mask_debug_active,
            draw_order=draw_order,
            layer=layer,
            world_state=world_state,
            shader_name=shader_name,
            effective_blend=effective_blend,
            mask_role=mask_role,
            mask_key=mask_key,
            stencil_write=False,
            stencil_read=bool(mask_consumer_mode != "none"),
            stencil_read_attempted=stencil_read_attempted,
        )
    
    def render_sprite(
        self,
        state: Dict,
        opacity: float,
        atlases: List[TextureAtlas],
        layer: LayerData = None,
        render: bool = True,
        shader_behavior: Optional[ShaderBehavior] = None,
        shader_name_override: Optional[str] = None,
        premultiply_color: bool = True,
    ) -> Optional[SpriteDrawInfo]:
        """
        Render just the sprite at origin (transforms already applied)
        
        The transformation matrix already handles all positioning, rotation, and scaling
        including mirroring from negative scale values. We just draw the sprite at origin.
        
        Args:
            state: Layer state containing sprite info and colors
            opacity: Opacity value (0-1)
            atlases: List of texture atlases to search
            layer: Layer data containing anchor information
        """
        # Get sprite
        sprite_name = state['sprite_name']
        if not sprite_name:
            return
        
        # Find sprite in atlases
        sprite, atlas, _ = self._find_sprite_in_atlases(sprite_name, atlases)
        if not sprite or not atlas or not atlas.texture_id:
            return None
        
        # Apply opacity and color.
        # Most viewer paths use premultiplied alpha blending; STRAIGHT_ALPHA mode keeps
        # RGB un-premultiplied so shader experiments can match Unity-style alpha blend.
        base_r = state['r'] / 255.0
        base_g = state['g'] / 255.0
        base_b = state['b'] / 255.0
        effective_shader_name = self._resolve_effective_shader_name(
            state,
            atlases,
            default_shader_name=shader_name_override,
        )
        preset = self._get_shader_preset(effective_shader_name)
        if preset:
            base_r = min(1.0, base_r * preset.color_scale[0])
            base_g = min(1.0, base_g * preset.color_scale[1])
            base_b = min(1.0, base_b * preset.color_scale[2])
            opacity = max(0.0, min(1.0, opacity * preset.alpha_scale))
        if shader_behavior:
            wave = shader_behavior.color_wave_multiplier(self.current_time)
            if wave:
                base_r = max(0.0, min(1.0, base_r * wave[0]))
                base_g = max(0.0, min(1.0, base_g * wave[1]))
                base_b = max(0.0, min(1.0, base_b * wave[2]))
                if wave[3] != 1.0:
                    opacity = max(0.0, min(1.0, opacity * wave[3]))
        if premultiply_color:
            r = base_r * opacity
            g = base_g * opacity
            b = base_b * opacity
        else:
            r = base_r
            g = base_g
            b = base_b
        glColor4f(r, g, b, opacity)
        
        # Bind texture
        glBindTexture(GL_TEXTURE_2D, atlas.texture_id)
        alpha_aware = bool(getattr(atlas, "pivot_mode", None) == "dof")
        if alpha_aware and self.dof_alpha_smoothing_enabled:
            shader_mode = (
                "dof_smooth_alpha_strong"
                if self.dof_alpha_smoothing_mode == "strong"
                else "dof_smooth_alpha"
            )
        else:
            shader_mode = self.texture_filter_mode if self.texture_filter_mode in ("bicubic", "lanczos") else None
        if shader_mode:
            self._apply_texture_filter(atlas.texture_id, shader_mode)
        else:
            self._apply_texture_filter(atlas.texture_id)
        anchor_dx = anchor_dy = 0.0
        anchor_offset = state.get('sprite_anchor_offset')
        if anchor_offset:
            anchor_dx = anchor_offset[0] * self.base_world_scale * self.position_scale
            anchor_dy = anchor_offset[1] * self.base_world_scale * self.position_scale

        geometry = self._build_polygon_geometry(sprite, atlas)
        if geometry:
            vertices, texcoords, triangles = geometry
            if anchor_dx or anchor_dy:
                vertices = [(x + anchor_dx, y + anchor_dy) for x, y in vertices]
            vert_count = len(vertices)
            if render:
                shader_active = False
                if shader_mode:
                    shader_active = self._use_filter_program(
                        shader_mode,
                        int(atlas.image_width),
                        int(atlas.image_height),
                        alpha_aware=alpha_aware,
                    )
                    if not shader_active:
                        self._apply_texture_filter(atlas.texture_id, "bilinear")
                glBegin(GL_TRIANGLES)
                for i in range(0, len(triangles), 3):
                    idx0 = triangles[i]
                    idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                    idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                    if idx1 is None or idx2 is None:
                        break
                    if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                        continue
                    u0, v0 = texcoords[idx0]
                    u1, v1 = texcoords[idx1]
                    u2, v2 = texcoords[idx2]
                    x0, y0 = vertices[idx0]
                    x1, y1 = vertices[idx1]
                    x2, y2 = vertices[idx2]
                    glTexCoord2f(u0, v0); glVertex2f(x0, y0)
                    glTexCoord2f(u1, v1); glVertex2f(x1, y1)
                    glTexCoord2f(u2, v2); glVertex2f(x2, y2)
                glEnd()
                if shader_active:
                    self._stop_filter_program()
            return SpriteDrawInfo(vertices, texcoords, list(triangles), (r, g, b, opacity), sprite, atlas)
        
        # Calculate texture coordinates
        tx1 = sprite.x / atlas.image_width
        ty1 = sprite.y / atlas.image_height
        tx2 = (sprite.x + sprite.w) / atlas.image_width
        ty2 = (sprite.y + sprite.h) / atlas.image_height
        
        # Get sprite dimensions from atlas
        atlas_w = sprite.w
        atlas_h = sprite.h
        
        # When rotated in atlas, the sprite is stored rotated 90° clockwise
        if sprite.rotated:
            sprite_w = atlas_h  # After un-rotating, width comes from atlas height
            sprite_h = atlas_w  # After un-rotating, height comes from atlas width
        else:
            sprite_w = atlas_w
            sprite_h = atlas_h
        
        # Apply hi-res scaling: hi-res sprites are 2x size, need to be scaled down by 0.5
        hires_scale = 0.5 if atlas.is_hires else 1.0
        sprite_w *= hires_scale
        sprite_h *= hires_scale
        
        # Apply hi-res scaling to offsets too
        if getattr(atlas, "pivot_mode", None) == "dof":
            offset_x = 0.0
            offset_y = 0.0
        else:
            offset_x = sprite.offset_x * hires_scale * self.trim_shift_multiplier
            offset_y = sprite.offset_y * hires_scale * self.trim_shift_multiplier
        
        # The sprite is drawn at the origin, but trimmed sprites need offset adjustment
        # The offset tells us where the trimmed sprite sits within the original canvas
        # Scale offset by position_scale first, then add anchor_dx (which is already scaled)
        sprite_x = offset_x * self.position_scale + anchor_dx
        sprite_y = offset_y * self.position_scale + anchor_dy
        sprite_w *= self.position_scale
        sprite_h *= self.position_scale
        
        # Draw the sprite quad
        x1 = sprite_x
        y1 = sprite_y
        x2 = x1 + sprite_w
        y2 = y1 + sprite_h
        if sprite.rotated:
            vertices = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            texcoords = [(tx2, ty1), (tx2, ty2), (tx1, ty2), (tx1, ty1)]
        else:
            vertices = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
            texcoords = [(tx1, ty1), (tx2, ty1), (tx2, ty2), (tx1, ty2)]
        if render:
            shader_active = False
            if shader_mode:
                shader_active = self._use_filter_program(
                    shader_mode,
                    int(atlas.image_width),
                    int(atlas.image_height),
                    alpha_aware=alpha_aware,
                )
                if not shader_active:
                    self._apply_texture_filter(atlas.texture_id, "bilinear")
            glBegin(GL_QUADS)
            for (u, v), (vx, vy) in zip(texcoords, vertices):
                glTexCoord2f(u, v)
                glVertex2f(vx, vy)
            glEnd()
            if shader_active:
                self._stop_filter_program()
        quad_triangles = [0, 1, 2, 0, 2, 3]
        return SpriteDrawInfo(vertices, texcoords, quad_triangles, (r, g, b, opacity), sprite, atlas)
    
    def is_point_in_layer(
        self,
        world_x: float,
        world_y: float,
        layer: LayerData,
        world_state: Dict,
        atlases: List[TextureAtlas],
        layer_offsets: Dict[int, Tuple[float, float]]
    ) -> bool:
        """
        Check if a point is inside a layer's sprite bounds using matrix inverse
        Used for interactive sprite dragging
        
        This must match exactly how render_sprite draws the sprite:
        - Sprite is drawn at (offset_x, offset_y) with size (sprite_w, sprite_h)
        - All values are scaled by position_scale and hires_scale
        
        Args:
            world_x: X coordinate in world space
            world_y: Y coordinate in world space
            layer: Layer to check
            world_state: Pre-calculated world state for the layer
            atlases: List of texture atlases
            layer_offsets: User-applied offsets
        
        Returns:
            True if point is inside layer bounds
        """
        # Get sprite info
        sprite_name = world_state['sprite_name']
        if not sprite_name:
            return False
        
        sprite, atlas, _ = self._find_sprite_in_atlases(sprite_name, atlases)
        if not sprite or not atlas:
            return False
        
        # Transform point from world space to local space using inverse matrix
        # Account for user offset (applied before the matrix in render_layer)
        user_offset_x, user_offset_y = layer_offsets.get(layer.layer_id, (0, 0))
        adjusted_x = world_x - user_offset_x
        adjusted_y = world_y - user_offset_y
        
        # Get world matrix components
        m00 = world_state['m00']
        m01 = world_state['m01']
        m10 = world_state['m10']
        m11 = world_state['m11']
        tx = world_state['tx']
        ty = world_state['ty']
        
        # Calculate inverse matrix determinant
        det = m00 * m11 - m01 * m10
        if abs(det) < 1e-10:
            return False  # Matrix is singular, can't invert
        
        # Inverse 2x3 matrix
        inv_m00 = m11 / det
        inv_m01 = -m01 / det
        inv_m10 = -m10 / det
        inv_m11 = m00 / det
        inv_tx = -(inv_m00 * tx + inv_m01 * ty)
        inv_ty = -(inv_m10 * tx + inv_m11 * ty)
        
        # Transform point to local space
        local_x = inv_m00 * adjusted_x + inv_m01 * adjusted_y + inv_tx
        local_y = inv_m10 * adjusted_x + inv_m11 * adjusted_y + inv_ty

        geometry = self._build_polygon_geometry(sprite, atlas)
        if geometry:
            vertices, _, triangles = geometry
            vert_count = len(vertices)
            for i in range(0, len(triangles), 3):
                idx0 = triangles[i]
                idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                if idx1 is None or idx2 is None:
                    break
                if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                    continue
                if self._point_in_triangle(
                    local_x, local_y,
                    vertices[idx0], vertices[idx1], vertices[idx2]
                ):
                    return True
            return False
        
        # Get sprite dimensions - must match render_sprite exactly
        if sprite.rotated:
            sprite_w = sprite.h  # After un-rotating
            sprite_h = sprite.w
        else:
            sprite_w = sprite.w
            sprite_h = sprite.h
        
        # Apply hi-res scaling (same as render_sprite)
        hires_scale = 0.5 if atlas.is_hires else 1.0
        sprite_w *= hires_scale
        sprite_h *= hires_scale
        
        # Apply hi-res scaling to offsets (same as render_sprite)
        offset_x = sprite.offset_x * hires_scale * self.trim_shift_multiplier
        offset_y = sprite.offset_y * hires_scale * self.trim_shift_multiplier
        
        # The sprite is drawn at (offset_x, offset_y) in local space
        # Scale everything by position_scale (same as render_sprite)
        sprite_x = offset_x * self.position_scale
        sprite_y = offset_y * self.position_scale
        sprite_w *= self.position_scale
        sprite_h *= self.position_scale
        
        # Check if point is in sprite bounds (matching render_sprite's quad)
        # x1 = sprite_x, y1 = sprite_y, x2 = sprite_x + sprite_w, y2 = sprite_y + sprite_h
        if (sprite_x <= local_x <= sprite_x + sprite_w and
            sprite_y <= local_y <= sprite_y + sprite_h):
            return True
        
        return False
    
    def write_log_to_file(self, filename: str = "sprite_positions_LAYER_ANCHOR.txt"):
        """Write collected log data to file"""
        if not self.log_data:
            print("No log data to write")
            return
        
        with open(filename, 'w') as f:
            f.write("=== SPRITE POSITION LOG (LAYER ANCHOR VERSION) ===\n")
            f.write(f"Total layers: {len(self.log_data)}\n\n")
            
            for entry in self.log_data:
                f.write(f"Layer: {entry['layer']} (ID {entry['layer_id']})\n")
                f.write(f"  Original Anchor: {entry['original_anchor']}\n")
                f.write(f"  Layer Anchor: {entry['layer_anchor']}\n")
                f.write(f"  Position: ({entry['position'][0]:.2f}, {entry['position'][1]:.2f})\n")
                f.write(f"  Rotation: {entry['rotation']:.2f}°\n")
                f.write(f"  Scale: ({entry['scale'][0]:.3f}, {entry['scale'][1]:.3f})\n")
                pivot_info = entry.get('pivot_adjustment')
                if pivot_info:
                    f.write("  Pivot Adjustment:\n")
                    f.write(f"    Sprite: {pivot_info.get('sprite')}\n")
                    resolved = pivot_info.get('resolved_sprite')
                    if resolved and resolved != pivot_info.get('sprite'):
                        f.write(f"    Resolved Sprite: {resolved}\n")
                    mapped_anchor = pivot_info.get('mapped_anchor')
                    if mapped_anchor is not None:
                        f.write(f"    Mapped Anchor: {mapped_anchor}\n")
                    mapped_anchor_effective = pivot_info.get('mapped_anchor_effective')
                    if mapped_anchor_effective is not None:
                        f.write(f"    Mapped Anchor Effective: {mapped_anchor_effective}\n")
                    f.write(f"    Pivot: {pivot_info.get('pivot')}\n")
                    f.write(f"    Sprite Size: {pivot_info.get('sprite_size')}\n")
                    f.write(f"    Original Anchor: {pivot_info.get('original_anchor')}\n")
                    f.write(f"    Adjusted Anchor: {pivot_info.get('adjusted_anchor')}\n")
                    anchor_offset = pivot_info.get('anchor_offset')
                    if anchor_offset is not None:
                        f.write(f"    Anchor Offset: {anchor_offset}\n")
                else:
                    anchor_offset = entry.get('sprite_anchor_offset')
                    if anchor_offset is not None:
                        f.write("  Pivot Adjustment:\n")
                        f.write(f"    Anchor Offset: {anchor_offset}\n")
                f.write("\n")
        
        print(f"Log written to {filename} ({len(self.log_data)} layers)")
        self.log_data.clear()

    def _get_shader_preset(self, shader_name: Optional[str]) -> Optional[ShaderPreset]:
        """Return a preset describing how to approximate a shader."""
        if not shader_name or not self.shader_registry:
            return None
        preset = self.shader_registry.get_preset(shader_name)
        if not preset:
            payload = self._build_unity_shader_payload(shader_name)
            if payload:
                self.shader_registry.set_runtime_override(shader_name, payload)
                preset = self.shader_registry.get_preset(shader_name)
        if not preset and shader_name not in self._missing_shader_warnings:
            print(f"[Shader] No preset defined for '{shader_name}'.")
            self._missing_shader_warnings.add(shader_name)
        return preset

    @staticmethod
    def _build_unity_shader_payload(shader_name: str) -> Optional[Dict[str, Any]]:
        """Return a heuristic preset payload for common Unity shader names."""
        if not shader_name:
            return None
        lowered = shader_name.strip().lower()
        if not lowered:
            return None
        if "transparent masked" in lowered:
            return {
                "display_name": shader_name,
                "blend_override": "STRAIGHT_ALPHA",
                "color_scale": [1.0, 1.0, 1.0],
                "alpha_scale": 1.0,
                "metadata": {
                    "mask_source": "base",
                    "use_base_alpha_mask": True,
                },
                "notes": (
                    "Unity transparent masked sprite approximation "
                    "(straight alpha + base alpha mask)."
                ),
            }
        blend_override: Optional[str] = None
        if "additive" in lowered or "/add" in lowered or " add" in lowered:
            blend_override = "ADDITIVE"
        elif "multiply" in lowered or "mul" in lowered:
            blend_override = "MULTIPLY"
        elif "screen" in lowered:
            blend_override = "SCREEN"
        elif "premult" in lowered or "premultiply" in lowered:
            blend_override = "PREMULT_ALPHA"
        elif "alpha" in lowered or "normal" in lowered:
            blend_override = "STANDARD"
        if blend_override is None:
            return None
        return {
            "display_name": shader_name,
            "blend_override": blend_override,
            "color_scale": [1.0, 1.0, 1.0],
            "alpha_scale": 1.0,
            "notes": "Unity shader heuristic (auto). Update in Settings > Shaders for accuracy.",
        }

    def _get_shader_behavior(self, shader_name: Optional[str]) -> Optional[ShaderBehavior]:
        if not shader_name or not self.shader_registry:
            return None
        return self.shader_registry.get_behavior(shader_name)

    @staticmethod
    def _blend_value_from_preset(preset: Optional[ShaderPreset]) -> Optional[int]:
        """Resolve blend override string to BlendMode value."""
        if not preset or not preset.blend_mode:
            return None
        name = str(preset.blend_mode).upper()
        mapping = {
            "STANDARD": BlendMode.STANDARD,
            "PREMULT_ALPHA": BlendMode.PREMULT_ALPHA,
            "PREMULT_ALPHA_ALT": BlendMode.PREMULT_ALPHA_ALT,
            "PREMULT_ALPHA_ALT2": BlendMode.PREMULT_ALPHA_ALT2,
            "STRAIGHT_ALPHA": BlendMode.STRAIGHT_ALPHA,
            "ADDITIVE": BlendMode.ADDITIVE,
            "MULTIPLY": BlendMode.MULTIPLY,
            "SCREEN": BlendMode.SCREEN,
            "INHERIT": BlendMode.INHERIT,
        }
        return mapping.get(name)

    @staticmethod
    def _resolve_effective_layer_blend(
        layer_blend: int,
        shader_blend_override: Optional[int],
    ) -> int:
        """
        Resolve final layer blend mode for rendering.

        For default alpha shader presets (standard/premult), keep explicit non-default
        layer blend values (e.g. additive on Sprite_light layers) so authored JSON
        blend data is not clobbered by generic shader metadata.
        """
        if shader_blend_override is None:
            return layer_blend

        default_alpha_modes = {
            BlendMode.STANDARD,
            BlendMode.PREMULT_ALPHA,
            BlendMode.PREMULT_ALPHA_ALT,
            BlendMode.PREMULT_ALPHA_ALT2,
        }
        if shader_blend_override in default_alpha_modes and layer_blend not in default_alpha_modes:
            return layer_blend
        return shader_blend_override

    def get_blend_mode_for_shader(self, shader_name: Optional[str]) -> int:
        """Return the resolved blend mode for a shader (falls back to STANDARD)."""
        preset = self._get_shader_preset(shader_name)
        blend = self._blend_value_from_preset(preset)
        if blend is None:
            return BlendMode.STANDARD
        return blend

    def set_shader_registry(self, registry: Optional[ShaderRegistry]):
        """Inject the shader registry so presets can be resolved at runtime."""
        self.shader_registry = registry
        self._missing_shader_warnings.clear()
        self.shader_texture_cache.clear()

    def set_costume_pivot_adjustment_enabled(self, enabled: bool):
        """Toggle whether costume sprite pivots override layer anchors."""
        self.costume_pivot_adjustment_enabled = bool(enabled)

    def _get_shader_texture(self, path: Optional[str]) -> Optional[ShaderOverlayTexture]:
        if not path:
            return None
        overlay = self.shader_texture_cache.get(path)
        if not overlay:
            overlay = ShaderOverlayTexture(path=path)
            self.shader_texture_cache[path] = overlay
        if overlay.ensure_loaded():
            return overlay
        return None

    def _render_shader_overlay(
        self,
        draw_info: Optional[SpriteDrawInfo],
        behavior: ShaderBehavior,
        preset: Optional[ShaderPreset]
    ):
        if not draw_info:
            return
        metadata = (preset.metadata if preset else {}) or {}
        texture_path = metadata.get("sequence_texture") or metadata.get("lut")
        if not texture_path:
            return
        overlay = self._get_shader_texture(texture_path)
        if not overlay or not overlay.texture_id:
            return
        frame = behavior.compute_frame(self.current_time)
        texture_size = (overlay.width, overlay.height)
        transformed_uvs = self._compute_overlay_uvs(draw_info, behavior, frame, texture_size)

        def _draw_single(uvs: List[Tuple[float, float]]) -> None:
            triangles = draw_info.triangles or []
            if triangles:
                glBegin(GL_TRIANGLES)
                vert_count = len(draw_info.vertices)
                for i in range(0, len(triangles), 3):
                    idx0 = triangles[i]
                    idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                    idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                    if idx1 is None or idx2 is None:
                        break
                    if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                        continue
                    for idx in (idx0, idx1, idx2):
                        u, v = uvs[idx]
                        vx, vy = draw_info.vertices[idx]
                        glTexCoord2f(u, v)
                        glVertex2f(vx, vy)
                glEnd()
                return
            primitive = GL_QUADS if len(draw_info.vertices) == 4 else GL_TRIANGLE_FAN
            glBegin(primitive)
            for (vx, vy), (u, v) in zip(draw_info.vertices, uvs):
                glTexCoord2f(u, v)
                glVertex2f(vx, vy)
            glEnd()

        def _draw_multi(
            overlay_uvs: List[Tuple[float, float]],
            mask_uvs: List[Tuple[float, float]]
        ) -> None:
            triangles = draw_info.triangles or []
            if triangles:
                glBegin(GL_TRIANGLES)
                vert_count = len(draw_info.vertices)
                for i in range(0, len(triangles), 3):
                    idx0 = triangles[i]
                    idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                    idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                    if idx1 is None or idx2 is None:
                        break
                    if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                        continue
                    for idx in (idx0, idx1, idx2):
                        u, v = overlay_uvs[idx]
                        mu, mv = mask_uvs[idx]
                        vx, vy = draw_info.vertices[idx]
                        glMultiTexCoord2f(GL_TEXTURE0, u, v)
                        glMultiTexCoord2f(GL_TEXTURE1, mu, mv)
                        glVertex2f(vx, vy)
                glEnd()
                return
            primitive = GL_QUADS if len(draw_info.vertices) == 4 else GL_TRIANGLE_FAN
            glBegin(primitive)
            for idx, (vx, vy) in enumerate(draw_info.vertices):
                u, v = overlay_uvs[idx]
                mu, mv = mask_uvs[idx]
                glMultiTexCoord2f(GL_TEXTURE0, u, v)
                glMultiTexCoord2f(GL_TEXTURE1, mu, mv)
                glVertex2f(vx, vy)
            glEnd()

        mask_path = None
        for key in (
            "sequence_mask",
            "mask_texture",
            "overlay_mask",
            "mask_path",
            "alpha_texture",
            "alpha_mask",
            "mask",
        ):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                mask_path = value.strip()
                break

        mask_overlay = self._get_shader_texture(mask_path) if mask_path else None
        mask_from_base = bool(
            metadata.get("mask_base")
            or metadata.get("use_base_alpha_mask")
            or metadata.get("mask_from_base")
        )
        mask_source = str(metadata.get("mask_source") or "").strip().lower()
        if mask_source in ("base", "sprite", "atlas", "base_atlas"):
            mask_from_base = True

        mask_texture_id: Optional[int] = None
        mask_size: Optional[Tuple[int, int]] = None
        mask_uvs: Optional[List[Tuple[float, float]]] = None
        if mask_overlay and mask_overlay.texture_id:
            mask_texture_id = mask_overlay.texture_id
            mask_size = (mask_overlay.width, mask_overlay.height)
            mask_uvs = list(draw_info.texcoords)
        elif mask_from_base and draw_info.atlas and draw_info.atlas.texture_id:
            mask_texture_id = draw_info.atlas.texture_id
            mask_size = (
                int(draw_info.atlas.image_width or 0),
                int(draw_info.atlas.image_height or 0),
            )
            mask_uvs = list(draw_info.texcoords)

        if mask_uvs:
            mask_uv_mode = str(metadata.get("mask_uv") or metadata.get("mask_uv_mode") or "").strip().lower()
            if mask_uv_mode in ("overlay", "sequence", "shader"):
                mask_uvs = list(transformed_uvs)

        alpha_aware = bool(getattr(draw_info.atlas, "pivot_mode", None) == "dof")
        if alpha_aware and self.dof_alpha_smoothing_enabled:
            shader_mode = (
                "dof_smooth_alpha_strong"
                if self.dof_alpha_smoothing_mode == "strong"
                else "dof_smooth_alpha"
            )
        else:
            shader_mode = self.texture_filter_mode if self.texture_filter_mode in ("bicubic", "lanczos") else None
        stencil_active = self._apply_overlay_stencil(draw_info)
        try:
            glColor4f(*draw_info.color)
            if mask_texture_id and mask_uvs:
                if shader_mode:
                    glActiveTexture(GL_TEXTURE0)
                    _set_texture_2d_enabled(True)
                    glBindTexture(GL_TEXTURE_2D, overlay.texture_id)
                    self._apply_texture_filter(overlay.texture_id, shader_mode)
                    glActiveTexture(GL_TEXTURE1)
                    _set_texture_2d_enabled(True)
                    glBindTexture(GL_TEXTURE_2D, mask_texture_id)
                    self._apply_texture_filter(mask_texture_id, shader_mode)
                    glActiveTexture(GL_TEXTURE0)
                    shader_active = self._use_filter_program_masked(
                        shader_mode,
                        texture_size,
                        mask_size or (1, 1),
                        alpha_aware=alpha_aware,
                    )
                    if shader_active:
                        _draw_multi(transformed_uvs, mask_uvs)
                        self._stop_filter_program()
                        glActiveTexture(GL_TEXTURE1)
                        glBindTexture(GL_TEXTURE_2D, 0)
                        _set_texture_2d_enabled(False)
                        glActiveTexture(GL_TEXTURE0)
                        glBindTexture(GL_TEXTURE_2D, 0)
                    else:
                        glActiveTexture(GL_TEXTURE1)
                        glBindTexture(GL_TEXTURE_2D, 0)
                        _set_texture_2d_enabled(False)
                        glActiveTexture(GL_TEXTURE0)
                        self._begin_overlay_textures(overlay.texture_id, mask_texture_id)
                        _draw_multi(transformed_uvs, mask_uvs)
                        self._end_overlay_textures(True)
                else:
                    self._begin_overlay_textures(overlay.texture_id, mask_texture_id)
                    _draw_multi(transformed_uvs, mask_uvs)
                    self._end_overlay_textures(True)
            else:
                glActiveTexture(GL_TEXTURE0)
                glBindTexture(GL_TEXTURE_2D, overlay.texture_id)
                if shader_mode:
                    self._apply_texture_filter(overlay.texture_id, shader_mode)
                    shader_active = self._use_filter_program(
                        shader_mode,
                        overlay.width,
                        overlay.height,
                        alpha_aware=alpha_aware,
                    )
                    if not shader_active:
                        self._apply_texture_filter(overlay.texture_id, "bilinear")
                    _draw_single(transformed_uvs)
                    if shader_active:
                        self._stop_filter_program()
                else:
                    self._apply_texture_filter(overlay.texture_id)
                    _draw_single(transformed_uvs)
        finally:
            self._restore_overlay_stencil(stencil_active)

    def _compute_overlay_uvs(
        self,
        draw_info: SpriteDrawInfo,
        behavior: ShaderBehavior,
        frame_index: float,
        texture_size: Tuple[int, int]
    ) -> List[Tuple[float, float]]:
        sprite = draw_info.sprite
        atlas = draw_info.atlas
        mode = (behavior.mapping_mode or "sprite").lower()
        if mode in ("sheet", "atlas", "projected"):
            frame = frame_index
            return [
                behavior.transform_uv(texcoord, frame, texture_size)
                for texcoord in draw_info.texcoords
            ]
        if (
            mode in ("sprite", "strip", "trimmed", "auto")
            and sprite
            and atlas
            and not getattr(sprite, "rotated", False)
            and atlas.image_width
            and atlas.image_height
            and sprite.w
            and sprite.h
        ):
            return self._sprite_aligned_overlay_uvs(
                draw_info,
                behavior,
                frame_index,
                atlas.image_width,
                atlas.image_height,
            )
        frame = frame_index
        return [
            behavior.transform_uv(texcoord, frame, texture_size)
            for texcoord in draw_info.texcoords
        ]

    def _sprite_aligned_overlay_uvs(
        self,
        draw_info: SpriteDrawInfo,
        behavior: ShaderBehavior,
        frame_index: float,
        atlas_width: int,
        atlas_height: int
    ) -> List[Tuple[float, float]]:
        sprite = draw_info.sprite
        if not sprite:
            return [(0.0, 0.0) for _ in draw_info.texcoords]
        sprite_w = max(1.0, float(sprite.w))
        sprite_h = max(1.0, float(sprite.h))
        start_x = float(sprite.x)
        start_y = float(sprite.y)
        frame_total = max(1, behavior.frame_count())
        frame_axis = (behavior.frame_axis or "u").lower()
        slot = int(round(frame_index))
        slot = max(0, min(frame_total - 1, slot))
        step = 1.0 / frame_total

        coords: List[Tuple[float, float]] = []
        for tex_u, tex_v in draw_info.texcoords:
            px = tex_u * atlas_width
            py = tex_v * atlas_height
            local_x = (px - start_x) / sprite_w
            local_y = (py - start_y) / sprite_h
            local_x = max(0.0, min(1.0, local_x))
            local_y = max(0.0, min(1.0, local_y))
            if frame_axis == "v":
                u = local_x
                v = slot * step + local_y * step
            else:
                u = slot * step + local_x * step
                v = local_y
            coords.append((max(0.0, min(1.0, u)), max(0.0, min(1.0, v))))
        return coords

    def _apply_overlay_stencil(self, draw_info: SpriteDrawInfo) -> bool:
        """Mask overlay rendering to the base sprite shape using the stencil buffer."""
        vertices = draw_info.vertices
        if not vertices:
            return False
        glPushAttrib(GL_STENCIL_BUFFER_BIT | GL_COLOR_BUFFER_BIT | GL_ENABLE_BIT)
        glEnable(GL_STENCIL_TEST)
        glClearStencil(0)
        glClear(GL_STENCIL_BUFFER_BIT)
        glStencilFunc(GL_ALWAYS, 1, 0xFF)
        glStencilOp(GL_REPLACE, GL_REPLACE, GL_REPLACE)
        glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE)
        _set_texture_2d_enabled(False)
        self._draw_overlay_geometry(vertices, draw_info.triangles)
        _set_texture_2d_enabled(True)
        glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE)
        glStencilFunc(GL_EQUAL, 1, 0xFF)
        glStencilOp(GL_KEEP, GL_KEEP, GL_KEEP)
        return True

    def _restore_overlay_stencil(self, active: bool) -> None:
        if not active:
            return
        glDisable(GL_STENCIL_TEST)
        glPopAttrib()

    def _draw_overlay_geometry(self, vertices: List[Tuple[float, float]], triangles: List[int]) -> None:
        if triangles:
            glBegin(GL_TRIANGLES)
            vert_count = len(vertices)
            for i in range(0, len(triangles), 3):
                idx0 = triangles[i]
                idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                if idx1 is None or idx2 is None:
                    break
                if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                    continue
                x0, y0 = vertices[idx0]
                x1, y1 = vertices[idx1]
                x2, y2 = vertices[idx2]
                glVertex2f(x0, y0)
                glVertex2f(x1, y1)
                glVertex2f(x2, y2)
            glEnd()
            return
        primitive = GL_QUADS if len(vertices) == 4 else GL_TRIANGLE_FAN
        glBegin(primitive)
        for vx, vy in vertices:
            glVertex2f(vx, vy)
        glEnd()
        glColor4f(*draw_info.color)
        triangles = draw_info.triangles or []
        if triangles:
            glBegin(GL_TRIANGLES)
            vert_count = len(draw_info.vertices)
            for i in range(0, len(triangles), 3):
                idx0 = triangles[i]
                idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                if idx1 is None or idx2 is None:
                    break
                if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                    continue
                self._emit_overlay_vertex(
                    idx0, draw_info.vertices, transformed_uvs, base_texcoords, use_mask
                )
                self._emit_overlay_vertex(
                    idx1, draw_info.vertices, transformed_uvs, base_texcoords, use_mask
                )
                self._emit_overlay_vertex(
                    idx2, draw_info.vertices, transformed_uvs, base_texcoords, use_mask
                )
            glEnd()
        else:
            primitive = GL_QUADS if len(draw_info.vertices) == 4 else GL_TRIANGLE_FAN
            glBegin(primitive)
            for idx in range(len(draw_info.vertices)):
                self._emit_overlay_vertex(
                    idx, draw_info.vertices, transformed_uvs, base_texcoords, use_mask
                )
            glEnd()
        self._end_overlay_textures(use_mask)

    @staticmethod
    def _draw_sprite_draw_info(draw_info: SpriteDrawInfo) -> None:
        triangles = draw_info.triangles or []
        if triangles:
            glBegin(GL_TRIANGLES)
            vert_count = len(draw_info.vertices)
            for i in range(0, len(triangles), 3):
                idx0 = triangles[i]
                idx1 = triangles[i + 1] if i + 1 < len(triangles) else None
                idx2 = triangles[i + 2] if i + 2 < len(triangles) else None
                if idx1 is None or idx2 is None:
                    break
                if idx0 >= vert_count or idx1 >= vert_count or idx2 >= vert_count:
                    continue
                u0, v0 = draw_info.texcoords[idx0]
                u1, v1 = draw_info.texcoords[idx1]
                u2, v2 = draw_info.texcoords[idx2]
                x0, y0 = draw_info.vertices[idx0]
                x1, y1 = draw_info.vertices[idx1]
                x2, y2 = draw_info.vertices[idx2]
                glTexCoord2f(u0, v0); glVertex2f(x0, y0)
                glTexCoord2f(u1, v1); glVertex2f(x1, y1)
                glTexCoord2f(u2, v2); glVertex2f(x2, y2)
            glEnd()
            return
        primitive = GL_QUADS if len(draw_info.vertices) == 4 else GL_TRIANGLE_FAN
        glBegin(primitive)
        for (u, v), (vx, vy) in zip(draw_info.texcoords, draw_info.vertices):
            glTexCoord2f(u, v)
            glVertex2f(vx, vy)
        glEnd()

    def _render_mask_source_layer(
        self,
        layer: LayerData,
        world_state: Dict,
        atlases: List[TextureAtlas],
        layer_offsets: Dict[int, Tuple[float, float]],
        mask_key: str,
        *,
        render_visible: bool = False,
    ) -> bool:
        glPushMatrix()
        offset_x, offset_y = layer_offsets.get(layer.layer_id, (0, 0))
        if offset_x or offset_y:
            glTranslatef(offset_x, offset_y, 0)
        matrix = [
            world_state['m00'], world_state['m10'], 0.0, 0.0,
            world_state['m01'], world_state['m11'], 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            world_state['tx'], world_state['ty'], 0.0, 1.0
        ]
        glMultMatrixf(matrix)
        mask_written = self._write_mask_to_stencil(world_state, atlases, layer)
        if render_visible:
            shader_name = self._resolve_effective_shader_name(world_state, atlases)
            shader_preset = self._get_shader_preset(shader_name)
            shader_behavior = self._get_shader_behavior(shader_name)
            blend_override = self._blend_value_from_preset(shader_preset)
            effective_blend = self._resolve_effective_layer_blend(layer.blend_mode, blend_override)
            premultiply_color = effective_blend != BlendMode.STRAIGHT_ALPHA
            set_blend_mode(effective_blend)
            draw_info = self.render_sprite(
                world_state,
                world_state.get('world_opacity', 1.0),
                atlases,
                layer,
                render=True,
                shader_behavior=shader_behavior,
                shader_name_override=shader_name,
                premultiply_color=premultiply_color,
            )
            if shader_behavior and draw_info:
                self._render_shader_overlay(draw_info, shader_behavior, shader_preset)
            reset_blend_mode()
        glPopMatrix()
        reset_blend_mode()
        if mask_written:
            self.pending_mask_key = mask_key
        else:
            self.pending_mask_key = None
            self._log_mask_warning(
                f"{mask_key}:source",
                f"Failed to capture mask geometry for layer '{layer.name}' (key {mask_key})."
            )
        return mask_written

    def _write_mask_to_stencil(
        self,
        state: Dict,
        atlases: List[TextureAtlas],
        layer: LayerData
    ) -> bool:
        # Preferred path: capture source alpha into a screen-space mask texture.
        try:
            vx, vy, vw, vh = self._current_viewport()
            if self._ensure_mask_resources(vw, vh) and self._mask_fbo and self._mask_texture:
                prev_fbo = int(self.framebuffer_binding_hint or 0)
                try:
                    glBindFramebuffer(GL_FRAMEBUFFER, int(self._mask_fbo))
                    glViewport(0, 0, int(vw), int(vh))
                    glClearColor(0.0, 0.0, 0.0, 0.0)
                    glClear(GL_COLOR_BUFFER_BIT)
                    glDisable(GL_BLEND)
                    try:
                        glDisable(GL_DITHER)
                    except Exception:
                        pass
                    # Write only alpha to the mask target. Keeps RGB untouched.
                    glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_TRUE)
                    draw_info = self.render_sprite(
                        state,
                        state.get('world_opacity', 1.0),
                        atlases,
                        layer,
                        render=True,
                    )
                    if draw_info is not None:
                        self._mask_capture_mode = "alpha"
                        return True
                finally:
                    try:
                        glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE)
                    except Exception:
                        pass
                    glBindFramebuffer(GL_FRAMEBUFFER, prev_fbo)
                    glViewport(int(vx), int(vy), int(vw), int(vh))
                    # Keep post-mask state deterministic for downstream layer draws.
                    glEnable(GL_BLEND)
                    glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        except Exception as exc:
            self._log_mask_warning(
                "alpha-mask-capture",
                f"Alpha mask capture failed ({exc}); falling back to stencil masking.",
            )
            # Fall through to stencil fallback path below.
            pass

        # Fallback path: binary stencil cutout.
        glPushAttrib(GL_STENCIL_BUFFER_BIT | GL_COLOR_BUFFER_BIT | GL_ENABLE_BIT)
        try:
            draw_info = self.render_sprite(
                state,
                state.get('world_opacity', 1.0),
                atlases,
                layer,
                render=False,
            )
            if draw_info is None or draw_info.atlas is None or not draw_info.atlas.texture_id:
                return False

            glEnable(GL_STENCIL_TEST)
            glClearStencil(0)
            glClear(GL_STENCIL_BUFFER_BIT)
            glStencilFunc(GL_ALWAYS, 1, 0xFF)
            glStencilOp(GL_REPLACE, GL_REPLACE, GL_REPLACE)
            glColorMask(GL_FALSE, GL_FALSE, GL_FALSE, GL_FALSE)
            glDisable(GL_BLEND)
            try:
                glDisable(GL_DITHER)
            except Exception:
                pass

            glBindTexture(GL_TEXTURE_2D, draw_info.atlas.texture_id)
            # Bilinear improves cutout stability; UV clamp below prevents atlas bleed.
            self._apply_texture_filter(draw_info.atlas.texture_id, "bilinear")
            glColor4f(1.0, 1.0, 1.0, 1.0)

            threshold = max(0.0, min(1.0, float(self.mask_stencil_alpha_threshold)))
            softness = max(0.0, min(0.5, float(self.mask_stencil_alpha_softness)))
            atlas_w = int(
                max(
                    1,
                    getattr(draw_info.atlas, "image_width", 0)
                    or getattr(draw_info.atlas, "logical_width", 0)
                    or 1,
                )
            )
            atlas_h = int(
                max(
                    1,
                    getattr(draw_info.atlas, "image_height", 0)
                    or getattr(draw_info.atlas, "logical_height", 0)
                    or 1,
                )
            )
            texel_size = (1.0 / float(atlas_w), 1.0 / float(atlas_h))
            uv_coords = draw_info.texcoords or []
            if uv_coords:
                u_vals = [uv[0] for uv in uv_coords]
                v_vals = [uv[1] for uv in uv_coords]
                u_min = max(0.0, min(1.0, min(u_vals)))
                u_max = max(0.0, min(1.0, max(u_vals)))
                v_min = max(0.0, min(1.0, min(v_vals)))
                v_max = max(0.0, min(1.0, max(v_vals)))
                # Clamp sampling inside the sprite UV rectangle to avoid bleeding adjacent
                # packed atlas sprites into mask generation.
                eps_u = 0.5 / float(atlas_w)
                eps_v = 0.5 / float(atlas_h)
                if (u_max - u_min) > (eps_u * 2.0):
                    u_min += eps_u
                    u_max -= eps_u
                if (v_max - v_min) > (eps_v * 2.0):
                    v_min += eps_v
                    v_max -= eps_v
                uv_min = (u_min, v_min)
                uv_max = (u_max, v_max)
            else:
                uv_min = (0.0, 0.0)
                uv_max = (1.0, 1.0)

            if self._use_alpha_stencil_program(
                threshold,
                softness,
                texel_size=texel_size,
                uv_min=uv_min,
                uv_max=uv_max,
            ):
                self._draw_sprite_draw_info(draw_info)
                self._stop_filter_program()
                self._mask_capture_mode = "stencil"
                return True

            # Fallback for contexts that fail shader compile/link.
            alpha_test_enabled = False
            try:
                glEnable(GL_ALPHA_TEST)
                glAlphaFunc(GL_GREATER, threshold)
                alpha_test_enabled = True
            except Exception:
                alpha_test_enabled = False
            self._draw_sprite_draw_info(draw_info)
            if alpha_test_enabled:
                glDisable(GL_ALPHA_TEST)
            self._mask_capture_mode = "stencil"
            return True
        finally:
            self._stop_filter_program()
            glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE)
            glDisable(GL_STENCIL_TEST)
            glPopAttrib()

    def _activate_mask_consumer(self, mask_key: str) -> str:
        if self.pending_mask_key != mask_key:
            self._log_mask_warning(
                f"{mask_key}:missing",
                f"Mask '{mask_key}' was not initialized before its consumer layer rendered."
            )
            return "none"
        if self._mask_capture_mode == "alpha" and self._mask_texture:
            return "alpha"
        if self._mask_capture_mode != "stencil":
            return "none"
        glPushAttrib(GL_STENCIL_BUFFER_BIT | GL_ENABLE_BIT)
        glEnable(GL_STENCIL_TEST)
        glStencilFunc(GL_EQUAL, 0, 0xFF)
        glStencilOp(GL_KEEP, GL_KEEP, GL_KEEP)
        return "stencil"

    def _deactivate_mask_consumer(self, mode: str) -> None:
        if mode == "none":
            return
        if mode == "stencil":
            glDisable(GL_STENCIL_TEST)
            glPopAttrib()
        self.pending_mask_key = None
        self._mask_capture_mode = "none"

    def _render_sprite_with_alpha_mask(self, draw_info: SpriteDrawInfo) -> None:
        if draw_info.atlas is None or not draw_info.atlas.texture_id or not self._mask_texture:
            return
        program = self._get_mask_consumer_program()
        if not program:
            # If shader compilation fails, fall back to regular draw.
            self._draw_sprite_draw_info(draw_info)
            return

        _, _, vw, vh = self._current_viewport()
        glUseProgram(program)
        if self._mask_consumer_uniform_texture is not None and self._mask_consumer_uniform_texture >= 0:
            glUniform1i(self._mask_consumer_uniform_texture, 0)
        if self._mask_consumer_uniform_mask is not None and self._mask_consumer_uniform_mask >= 0:
            glUniform1i(self._mask_consumer_uniform_mask, 1)
        if self._mask_consumer_uniform_view_size is not None and self._mask_consumer_uniform_view_size >= 0:
            glUniform2f(self._mask_consumer_uniform_view_size, float(max(1, vw)), float(max(1, vh)))

        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, int(draw_info.atlas.texture_id))
        glActiveTexture(GL_TEXTURE1)
        glBindTexture(GL_TEXTURE_2D, int(self._mask_texture))
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glActiveTexture(GL_TEXTURE0)

        self._draw_sprite_draw_info(draw_info)
        glUseProgram(0)
        glActiveTexture(GL_TEXTURE1)
        glBindTexture(GL_TEXTURE_2D, 0)
        glActiveTexture(GL_TEXTURE0)

    def _log_mask_warning(self, key: str, message: str) -> None:
        if key in self._mask_warning_messages:
            return
        self._mask_warning_messages.add(key)
        print(f"[Mask] {message}")

    def _log_cycle_warning(
        self,
        layer: LayerData,
        parent_layer: Optional[LayerData]
    ) -> None:
        parent_id = parent_layer.layer_id if parent_layer else -1
        key = (layer.layer_id, parent_id)
        if key in self._cycle_warning_keys:
            return
        self._cycle_warning_keys.add(key)
        parent_name = parent_layer.name if parent_layer else "unknown"
        child_name = layer.name or f"layer_{layer.layer_id}"
        print(
            f"[Renderer] Detected cyclic parenting: {child_name} (id {layer.layer_id}) "
            f"-> {parent_name} (id {parent_id}). Treating as parentless."
        )

    def _emit_overlay_vertex(
        self,
        index: int,
        vertices: List[Tuple[float, float]],
        overlay_uvs: List[Tuple[float, float]],
        base_uvs: List[Tuple[float, float]],
        use_mask: bool
    ) -> None:
        u, v = overlay_uvs[index]
        vx, vy = vertices[index]
        glMultiTexCoord2f(GL_TEXTURE0, u, v)
        if use_mask:
            bu, bv = base_uvs[index]
            glMultiTexCoord2f(GL_TEXTURE1, bu, bv)
        glVertex2f(vx, vy)

    def _begin_overlay_textures(self, overlay_id: int, mask_id: Optional[int]) -> None:
        glActiveTexture(GL_TEXTURE0)
        _set_texture_2d_enabled(True)
        glBindTexture(GL_TEXTURE_2D, overlay_id)
        if self.texture_filter_mode in ("bicubic", "lanczos"):
            self._apply_texture_filter(overlay_id, "bilinear")
        else:
            self._apply_texture_filter(overlay_id)
        glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        if mask_id:
            glActiveTexture(GL_TEXTURE1)
            _set_texture_2d_enabled(True)
            glBindTexture(GL_TEXTURE_2D, mask_id)
            if self.texture_filter_mode in ("bicubic", "lanczos"):
                self._apply_texture_filter(mask_id, "bilinear")
            else:
                self._apply_texture_filter(mask_id)
            glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_COMBINE)
            glTexEnvi(GL_TEXTURE_ENV, GL_COMBINE_RGB, GL_MODULATE)
            glTexEnvi(GL_TEXTURE_ENV, GL_SOURCE0_RGB, GL_PREVIOUS)
            glTexEnvi(GL_TEXTURE_ENV, GL_OPERAND0_RGB, GL_SRC_COLOR)
            glTexEnvi(GL_TEXTURE_ENV, GL_SOURCE1_RGB, GL_TEXTURE)
            glTexEnvi(GL_TEXTURE_ENV, GL_OPERAND1_RGB, GL_SRC_ALPHA)
            glTexEnvi(GL_TEXTURE_ENV, GL_COMBINE_ALPHA, GL_REPLACE)
            glTexEnvi(GL_TEXTURE_ENV, GL_SOURCE0_ALPHA, GL_TEXTURE)
            glTexEnvi(GL_TEXTURE_ENV, GL_OPERAND0_ALPHA, GL_SRC_ALPHA)
        glActiveTexture(GL_TEXTURE0)

    def _end_overlay_textures(self, used_mask: bool) -> None:
        if used_mask:
            glActiveTexture(GL_TEXTURE1)
            glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
            glBindTexture(GL_TEXTURE_2D, 0)
            _set_texture_2d_enabled(False)
            glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, 0)

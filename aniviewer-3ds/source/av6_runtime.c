#include "av6_runtime.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define AV6_MAGIC "AV6A"

typedef struct {
    FILE *fp;
    char *err;
    size_t err_size;
} Reader;

static void set_error(Reader *reader, const char *fmt, ...) {
    if (!reader || !reader->err || reader->err_size == 0) {
        return;
    }
    va_list args;
    va_start(args, fmt);
    vsnprintf(reader->err, reader->err_size, fmt, args);
    va_end(args);
}

static bool read_bytes(Reader *reader, void *dst, size_t size) {
    if (fread(dst, 1, size, reader->fp) != size) {
        set_error(reader, "Unexpected EOF while reading package");
        return false;
    }
    return true;
}

static bool read_u16(Reader *reader, uint16_t *out_value) {
    uint8_t raw[2];
    if (!read_bytes(reader, raw, sizeof(raw))) {
        return false;
    }
    *out_value = (uint16_t)(raw[0] | ((uint16_t)raw[1] << 8));
    return true;
}

static bool read_u32(Reader *reader, uint32_t *out_value) {
    uint8_t raw[4];
    if (!read_bytes(reader, raw, sizeof(raw))) {
        return false;
    }
    *out_value = (uint32_t)raw[0]
        | ((uint32_t)raw[1] << 8)
        | ((uint32_t)raw[2] << 16)
        | ((uint32_t)raw[3] << 24);
    return true;
}

static bool read_i32(Reader *reader, int32_t *out_value) {
    uint32_t value = 0;
    if (!read_u32(reader, &value)) {
        return false;
    }
    *out_value = (int32_t)value;
    return true;
}

static bool read_f32(Reader *reader, float *out_value) {
    uint32_t raw = 0;
    if (!read_u32(reader, &raw)) {
        return false;
    }
    union {
        uint32_t u;
        float f;
    } cvt;
    cvt.u = raw;
    *out_value = cvt.f;
    return true;
}

static bool read_string(Reader *reader, char **out_text) {
    uint16_t len = 0;
    if (!read_u16(reader, &len)) {
        return false;
    }

    char *text = (char *)calloc((size_t)len + 1u, 1u);
    if (!text) {
        set_error(reader, "Out of memory while reading string");
        return false;
    }

    if (len > 0 && !read_bytes(reader, text, (size_t)len)) {
        free(text);
        return false;
    }

    text[len] = '\0';
    *out_text = text;
    return true;
}

static float lerp_f32(float a, float b, float t) {
    return a + (b - a) * t;
}

static uint8_t lerp_u8(uint8_t a, uint8_t b, float t) {
    float out = lerp_f32((float)a, (float)b, t);
    if (out < 0.0f) {
        out = 0.0f;
    }
    if (out > 255.0f) {
        out = 255.0f;
    }
    return (uint8_t)(out + 0.5f);
}

typedef enum {
    AV6_F32_POS_X = 0,
    AV6_F32_POS_Y = 1,
    AV6_F32_SCALE_X = 2,
    AV6_F32_SCALE_Y = 3,
    AV6_F32_ROTATION = 4,
    AV6_F32_OPACITY = 5,
} Av6F32Channel;

typedef enum {
    AV6_U8_R = 0,
    AV6_U8_G = 1,
    AV6_U8_B = 2,
    AV6_U8_A = 3,
} Av6U8Channel;

static int8_t get_f32_immediate(const Av6Keyframe *keyframe, Av6F32Channel channel) {
    if (!keyframe) {
        return -1;
    }
    switch (channel) {
        case AV6_F32_POS_X:
        case AV6_F32_POS_Y:
            return keyframe->immediate_pos;
        case AV6_F32_SCALE_X:
        case AV6_F32_SCALE_Y:
            return keyframe->immediate_scale;
        case AV6_F32_ROTATION:
            return keyframe->immediate_rotation;
        case AV6_F32_OPACITY:
            return keyframe->immediate_opacity;
    }
    return -1;
}

static float get_f32_value(const Av6Keyframe *keyframe, Av6F32Channel channel) {
    if (!keyframe) {
        return 0.0f;
    }
    switch (channel) {
        case AV6_F32_POS_X:
            return keyframe->x;
        case AV6_F32_POS_Y:
            return keyframe->y;
        case AV6_F32_SCALE_X:
            return keyframe->scale_x;
        case AV6_F32_SCALE_Y:
            return keyframe->scale_y;
        case AV6_F32_ROTATION:
            return keyframe->rotation;
        case AV6_F32_OPACITY:
            return keyframe->opacity;
    }
    return 0.0f;
}

static uint8_t get_u8_value(const Av6Keyframe *keyframe, Av6U8Channel channel) {
    if (!keyframe) {
        return 0u;
    }
    switch (channel) {
        case AV6_U8_R:
            return keyframe->r;
        case AV6_U8_G:
            return keyframe->g;
        case AV6_U8_B:
            return keyframe->b;
        case AV6_U8_A:
            return keyframe->a;
    }
    return 0u;
}

static float eval_sparse_channel_f32(
    const Av6Package *pkg,
    uint32_t first,
    uint32_t last,
    float time_sec,
    Av6F32Channel channel,
    float default_value
) {
    const Av6Keyframe *first_valid = NULL;
    const Av6Keyframe *prev = NULL;
    const Av6Keyframe *next = NULL;

    if (!pkg || !pkg->keyframes || first > last || last >= pkg->keyframe_count) {
        return default_value;
    }

    for (uint32_t i = first; i <= last; i++) {
        const Av6Keyframe *keyframe = &pkg->keyframes[i];
        int8_t immediate = get_f32_immediate(keyframe, channel);
        if (immediate == -1) {
            continue;
        }

        if (!first_valid) {
            first_valid = keyframe;
        }

        if (keyframe->time <= time_sec) {
            prev = keyframe;
        } else if (!next) {
            next = keyframe;
            break;
        }
    }

    if (!first_valid) {
        return default_value;
    }
    if (!prev) {
        return get_f32_value(first_valid, channel);
    }

    {
        int8_t prev_immediate = get_f32_immediate(prev, channel);
        float prev_value = get_f32_value(prev, channel);

        if (prev_immediate == 1 || !next) {
            return prev_value;
        }
        if (prev_immediate == 0) {
            float span = next->time - prev->time;
            if (span > 0.00001f) {
                float t = (time_sec - prev->time) / span;
                if (t < 0.0f) {
                    t = 0.0f;
                } else if (t > 1.0f) {
                    t = 1.0f;
                }
                return lerp_f32(prev_value, get_f32_value(next, channel), t);
            }
        }
        return prev_value;
    }
}

static uint8_t eval_sparse_channel_u8(
    const Av6Package *pkg,
    uint32_t first,
    uint32_t last,
    float time_sec,
    Av6U8Channel channel,
    uint8_t default_value
) {
    const Av6Keyframe *first_valid = NULL;
    const Av6Keyframe *prev = NULL;
    const Av6Keyframe *next = NULL;

    if (!pkg || !pkg->keyframes || first > last || last >= pkg->keyframe_count) {
        return default_value;
    }

    for (uint32_t i = first; i <= last; i++) {
        const Av6Keyframe *keyframe = &pkg->keyframes[i];
        if (keyframe->immediate_rgb == -1) {
            continue;
        }

        if (!first_valid) {
            first_valid = keyframe;
        }

        if (keyframe->time <= time_sec) {
            prev = keyframe;
        } else if (!next) {
            next = keyframe;
            break;
        }
    }

    if (!first_valid) {
        return default_value;
    }
    if (!prev) {
        return get_u8_value(first_valid, channel);
    }

    if (prev->immediate_rgb == 1 || !next) {
        return get_u8_value(prev, channel);
    }
    if (prev->immediate_rgb == 0) {
        float span = next->time - prev->time;
        if (span > 0.00001f) {
            float t = (time_sec - prev->time) / span;
            if (t < 0.0f) {
                t = 0.0f;
            } else if (t > 1.0f) {
                t = 1.0f;
            }
            return lerp_u8(get_u8_value(prev, channel), get_u8_value(next, channel), t);
        }
    }
    return get_u8_value(prev, channel);
}

static uint16_t eval_sparse_sprite_index(
    const Av6Package *pkg,
    uint32_t first,
    uint32_t last,
    float time_sec,
    uint16_t default_value
) {
    const Av6Keyframe *first_valid = NULL;
    const Av6Keyframe *prev = NULL;
    const Av6Keyframe *next = NULL;

    if (!pkg || !pkg->keyframes || first > last || last >= pkg->keyframe_count) {
        return default_value;
    }

    for (uint32_t i = first; i <= last; i++) {
        const Av6Keyframe *keyframe = &pkg->keyframes[i];
        if (keyframe->immediate_sprite == -1) {
            continue;
        }

        if (!first_valid) {
            first_valid = keyframe;
        }

        if (keyframe->time <= time_sec) {
            prev = keyframe;
        } else if (!next) {
            next = keyframe;
            break;
        }
    }

    if (!first_valid) {
        return default_value;
    }
    if (!prev) {
        return first_valid->sprite_index;
    }
    if (prev->immediate_sprite == 0 && next) {
        float span = next->time - prev->time;
        if (span > 0.00001f) {
            float t = (time_sec - prev->time) / span;
            if (t >= 0.999f) {
                return next->sprite_index;
            }
        }
    }
    return prev->sprite_index;
}

void av6_free_package(Av6Package *pkg) {
    if (!pkg) {
        return;
    }

    if (pkg->sprites) {
        for (uint32_t i = 0; i < pkg->sprite_count; i++) {
            free(pkg->sprites[i].name);
            pkg->sprites[i].name = NULL;
        }
        free(pkg->sprites);
        pkg->sprites = NULL;
    }

    if (pkg->layers) {
        for (uint32_t i = 0; i < pkg->layer_count; i++) {
            free(pkg->layers[i].name);
            pkg->layers[i].name = NULL;
        }
        free(pkg->layers);
        pkg->layers = NULL;
    }

    free(pkg->keyframes);
    pkg->keyframes = NULL;

    free(pkg->sheet_name);
    pkg->sheet_name = NULL;

    if (pkg->external_sheet_paths) {
        for (uint32_t i = 0; i < pkg->external_sheet_count; i++) {
            free(pkg->external_sheet_paths[i]);
            pkg->external_sheet_paths[i] = NULL;
        }
        free(pkg->external_sheet_paths);
        pkg->external_sheet_paths = NULL;
    }
    pkg->external_sheet_count = 0;

    free(pkg->texture_blob);
    pkg->texture_blob = NULL;
    pkg->texture_blob_size = 0;

    pkg->version = 0;
    pkg->sprite_count = 0;
    pkg->layer_count = 0;
    pkg->keyframe_count = 0;
    pkg->duration = 0.0f;
    pkg->anim_width = 0.0f;
    pkg->anim_height = 0.0f;
    pkg->loop_offset = 0.0f;
    pkg->centered = 1u;
}

bool av6_load_package(const char *path, Av6Package *out_pkg, char *err, size_t err_size) {
    if (!out_pkg) {
        return false;
    }
    memset(out_pkg, 0, sizeof(*out_pkg));

    if (err && err_size > 0) {
        err[0] = '\0';
    }

    FILE *fp = fopen(path, "rb");
    if (!fp) {
        if (err && err_size > 0) {
            snprintf(err, err_size, "Could not open package: %s", path ? path : "(null)");
        }
        return false;
    }

    Reader reader = {
        .fp = fp,
        .err = err,
        .err_size = err_size,
    };

    char magic[4] = {0};
    if (!read_bytes(&reader, magic, sizeof(magic))) {
        fclose(fp);
        av6_free_package(out_pkg);
        return false;
    }
    if (memcmp(magic, AV6_MAGIC, 4) != 0) {
        set_error(&reader, "Invalid package magic (expected AV6A)");
        fclose(fp);
        av6_free_package(out_pkg);
        return false;
    }

    if (!read_u32(&reader, &out_pkg->version)
        || !read_u32(&reader, &out_pkg->sprite_count)
        || !read_u32(&reader, &out_pkg->layer_count)
        || !read_u32(&reader, &out_pkg->keyframe_count)
        || !read_f32(&reader, &out_pkg->duration)
        || !read_f32(&reader, &out_pkg->anim_width)
        || !read_f32(&reader, &out_pkg->anim_height)
        || !read_f32(&reader, &out_pkg->loop_offset)) {
        fclose(fp);
        av6_free_package(out_pkg);
        return false;
    }

    if (out_pkg->version == 1u) {
        out_pkg->texture_blob_size = 0;
    } else if (out_pkg->version == 2u) {
        if (!read_u32(&reader, &out_pkg->texture_blob_size)) {
            fclose(fp);
            av6_free_package(out_pkg);
            return false;
        }
    } else {
        set_error(&reader, "Unsupported package version: %u", out_pkg->version);
        fclose(fp);
        av6_free_package(out_pkg);
        return false;
    }

    if (!read_string(&reader, &out_pkg->sheet_name)) {
        fclose(fp);
        av6_free_package(out_pkg);
        return false;
    }

    /* AV6 package schema omits explicit centered flag; monster timelines are centered by default. */
    out_pkg->centered = 1u;

    out_pkg->sprites = (Av6Sprite *)calloc(out_pkg->sprite_count, sizeof(Av6Sprite));
    out_pkg->layers = (Av6Layer *)calloc(out_pkg->layer_count, sizeof(Av6Layer));
    out_pkg->keyframes = (Av6Keyframe *)calloc(out_pkg->keyframe_count, sizeof(Av6Keyframe));
    if ((!out_pkg->sprites && out_pkg->sprite_count > 0)
        || (!out_pkg->layers && out_pkg->layer_count > 0)
        || (!out_pkg->keyframes && out_pkg->keyframe_count > 0)) {
        set_error(&reader, "Out of memory while allocating package buffers");
        fclose(fp);
        av6_free_package(out_pkg);
        return false;
    }

    for (uint32_t i = 0; i < out_pkg->sprite_count; i++) {
        Av6Sprite *sprite = &out_pkg->sprites[i];
        sprite->sheet_index = 0u;
        sprite->rotated = 0u;
        sprite->draw_scale = 1.0f;

        if (out_pkg->version == 1u) {
            if (!read_string(&reader, &sprite->name)
                || !read_f32(&reader, &sprite->x)
                || !read_f32(&reader, &sprite->y)
                || !read_f32(&reader, &sprite->w)
                || !read_f32(&reader, &sprite->h)
                || !read_f32(&reader, &sprite->frame_x)
                || !read_f32(&reader, &sprite->frame_y)
                || !read_f32(&reader, &sprite->frame_w)
                || !read_f32(&reader, &sprite->frame_h)) {
                fclose(fp);
                av6_free_package(out_pkg);
                return false;
            }
        } else {
            if (!read_f32(&reader, &sprite->x)
                || !read_f32(&reader, &sprite->y)
                || !read_f32(&reader, &sprite->w)
                || !read_f32(&reader, &sprite->h)
                || !read_f32(&reader, &sprite->frame_x)
                || !read_f32(&reader, &sprite->frame_y)
                || !read_f32(&reader, &sprite->frame_w)
                || !read_f32(&reader, &sprite->frame_h)
                || !read_string(&reader, &sprite->name)) {
                fclose(fp);
                av6_free_package(out_pkg);
                return false;
            }
        }
    }

    for (uint32_t i = 0; i < out_pkg->layer_count; i++) {
        Av6Layer *layer = &out_pkg->layers[i];
        if (!read_i32(&reader, &layer->id)
            || !read_i32(&reader, &layer->parent_id)
            || !read_u32(&reader, &layer->blend)
            || !read_u32(&reader, &layer->first_keyframe)
            || !read_u32(&reader, &layer->keyframe_count)
            || !read_string(&reader, &layer->name)) {
            fclose(fp);
            av6_free_package(out_pkg);
            return false;
        }
        layer->anchor_x = 0.0f;
        layer->anchor_y = 0.0f;

        if (layer->first_keyframe > out_pkg->keyframe_count) {
            set_error(&reader, "Layer first_keyframe out of bounds");
            fclose(fp);
            av6_free_package(out_pkg);
            return false;
        }
        if (layer->first_keyframe + layer->keyframe_count > out_pkg->keyframe_count) {
            set_error(&reader, "Layer keyframe range out of bounds");
            fclose(fp);
            av6_free_package(out_pkg);
            return false;
        }
    }

    for (uint32_t i = 0; i < out_pkg->keyframe_count; i++) {
        Av6Keyframe *keyframe = &out_pkg->keyframes[i];
        if (!read_u32(&reader, &keyframe->layer_index)
            || !read_f32(&reader, &keyframe->time)
            || !read_f32(&reader, &keyframe->x)
            || !read_f32(&reader, &keyframe->y)
            || !read_f32(&reader, &keyframe->scale_x)
            || !read_f32(&reader, &keyframe->scale_y)
            || !read_f32(&reader, &keyframe->rotation)
            || !read_f32(&reader, &keyframe->opacity)
            || !read_bytes(&reader, &keyframe->r, 1)
            || !read_bytes(&reader, &keyframe->g, 1)
            || !read_bytes(&reader, &keyframe->b, 1)
            || !read_bytes(&reader, &keyframe->a, 1)
            || !read_u16(&reader, &keyframe->sprite_index)) {
            fclose(fp);
            av6_free_package(out_pkg);
            return false;
        }

        keyframe->immediate_pos = 0;
        keyframe->immediate_scale = 0;
        keyframe->immediate_rotation = 0;
        keyframe->immediate_opacity = 0;
        keyframe->immediate_sprite = 1;
        keyframe->immediate_rgb = 0;
    }

    if (out_pkg->texture_blob_size > 0u) {
        out_pkg->texture_blob = (uint8_t *)malloc((size_t)out_pkg->texture_blob_size);
        if (!out_pkg->texture_blob) {
            set_error(&reader, "Out of memory while allocating texture blob");
            fclose(fp);
            av6_free_package(out_pkg);
            return false;
        }
        if (!read_bytes(&reader, out_pkg->texture_blob, (size_t)out_pkg->texture_blob_size)) {
            fclose(fp);
            av6_free_package(out_pkg);
            return false;
        }
    }

    fclose(fp);
    return true;
}

static Av6Pose pose_from_keyframe(const Av6Keyframe *keyframe) {
    Av6Pose pose;
    memset(&pose, 0, sizeof(pose));
    if (!keyframe) {
        return pose;
    }

    pose.valid = true;
    pose.x = keyframe->x;
    pose.y = keyframe->y;
    pose.scale_x = keyframe->scale_x;
    pose.scale_y = keyframe->scale_y;
    pose.rotation = keyframe->rotation;
    pose.opacity = keyframe->opacity;
    pose.r = keyframe->r;
    pose.g = keyframe->g;
    pose.b = keyframe->b;
    pose.a = keyframe->a;
    pose.sprite_index = keyframe->sprite_index;
    return pose;
}

Av6Pose av6_eval_layer_pose(const Av6Package *pkg, uint32_t layer_index, float time_sec) {
    Av6Pose out_pose;
    memset(&out_pose, 0, sizeof(out_pose));

    if (!pkg || layer_index >= pkg->layer_count || !pkg->layers || !pkg->keyframes) {
        return out_pose;
    }

    const Av6Layer *layer = &pkg->layers[layer_index];
    if (layer->keyframe_count == 0u) {
        return out_pose;
    }

    uint32_t first = layer->first_keyframe;
    uint32_t last = first + layer->keyframe_count - 1u;
    if (last >= pkg->keyframe_count) {
        return out_pose;
    }

    const Av6Keyframe *first_kf = &pkg->keyframes[first];

    out_pose = pose_from_keyframe(first_kf);
    out_pose.valid = true;

    out_pose.x = eval_sparse_channel_f32(pkg, first, last, time_sec, AV6_F32_POS_X, out_pose.x);
    out_pose.y = eval_sparse_channel_f32(pkg, first, last, time_sec, AV6_F32_POS_Y, out_pose.y);
    out_pose.scale_x = eval_sparse_channel_f32(pkg, first, last, time_sec, AV6_F32_SCALE_X, out_pose.scale_x);
    out_pose.scale_y = eval_sparse_channel_f32(pkg, first, last, time_sec, AV6_F32_SCALE_Y, out_pose.scale_y);
    out_pose.rotation = eval_sparse_channel_f32(pkg, first, last, time_sec, AV6_F32_ROTATION, out_pose.rotation);
    out_pose.opacity = eval_sparse_channel_f32(pkg, first, last, time_sec, AV6_F32_OPACITY, out_pose.opacity);

    out_pose.r = eval_sparse_channel_u8(pkg, first, last, time_sec, AV6_U8_R, out_pose.r);
    out_pose.g = eval_sparse_channel_u8(pkg, first, last, time_sec, AV6_U8_G, out_pose.g);
    out_pose.b = eval_sparse_channel_u8(pkg, first, last, time_sec, AV6_U8_B, out_pose.b);
    out_pose.a = eval_sparse_channel_u8(pkg, first, last, time_sec, AV6_U8_A, out_pose.a);

    out_pose.sprite_index = eval_sparse_sprite_index(pkg, first, last, time_sec, out_pose.sprite_index);
    return out_pose;
}

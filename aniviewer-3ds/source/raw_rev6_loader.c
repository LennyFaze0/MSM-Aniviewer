#include "raw_rev6_loader.h"

#include <ctype.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define PATH_BUF_SIZE 1024
#define RAW_MAX_SOURCES 128u
#define RAW_MAX_ANIMS 1024u
#define RAW_MAX_LAYERS 4096u
#define RAW_MAX_FRAMES_PER_LAYER 200000u

typedef struct {
    const uint8_t *data;
    size_t size;
    size_t pos;
    char *err;
    size_t err_size;
} RawReader;

typedef struct {
    uint16_t id;
    char *src;
} RawSource;

typedef struct {
    RawSource *items;
    uint32_t count;
} RawSourceList;

typedef struct {
    Av6Sprite *items;
    uint32_t count;
    uint32_t cap;
} SpriteVec;

typedef struct {
    Av6Keyframe *items;
    uint32_t count;
    uint32_t cap;
} KeyframeVec;

typedef struct {
    uint16_t source_id;
    uint16_t sprite_index;
    char *name;
} SpriteLookup;

typedef struct {
    SpriteLookup *items;
    uint32_t count;
    uint32_t cap;
} LookupVec;

typedef struct {
    char **items;
    uint32_t count;
    uint32_t cap;
} StringVec;

static void set_error(char *err, size_t err_size, const char *fmt, ...) {
    va_list args;

    if (!err || err_size == 0u) {
        return;
    }

    va_start(args, fmt);
    vsnprintf(err, err_size, fmt, args);
    va_end(args);
}

static char *dup_text(const char *text) {
    size_t len;
    char *out;

    if (!text) {
        return NULL;
    }

    len = strlen(text);
    out = (char *)malloc(len + 1u);
    if (!out) {
        return NULL;
    }

    memcpy(out, text, len + 1u);
    return out;
}

static bool file_exists(const char *path) {
    FILE *fp;

    if (!path || !path[0]) {
        return false;
    }

    fp = fopen(path, "rb");
    if (!fp) {
        return false;
    }

    fclose(fp);
    return true;
}

static bool load_file_blob(const char *path, uint8_t **out_data, size_t *out_size, char *err, size_t err_size) {
    FILE *fp;
    long size_long;
    size_t size;
    uint8_t *data;

    *out_data = NULL;
    *out_size = 0u;

    fp = fopen(path, "rb");
    if (!fp) {
        set_error(err, err_size, "Could not open file: %s", path ? path : "(null)");
        return false;
    }

    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        set_error(err, err_size, "Failed to seek file: %s", path);
        return false;
    }

    size_long = ftell(fp);
    if (size_long < 0) {
        fclose(fp);
        set_error(err, err_size, "Failed to measure file: %s", path);
        return false;
    }

    if (fseek(fp, 0, SEEK_SET) != 0) {
        fclose(fp);
        set_error(err, err_size, "Failed to rewind file: %s", path);
        return false;
    }

    size = (size_t)size_long;
    data = (uint8_t *)malloc(size + 1u);
    if (!data) {
        fclose(fp);
        set_error(err, err_size, "Out of memory loading file: %s", path);
        return false;
    }

    if (size > 0u && fread(data, 1u, size, fp) != size) {
        free(data);
        fclose(fp);
        set_error(err, err_size, "Failed to read file: %s", path);
        return false;
    }

    fclose(fp);
    data[size] = 0u;

    *out_data = data;
    *out_size = size;
    return true;
}

static bool reader_align(RawReader *reader, size_t alignment) {
    size_t rem;
    size_t next;

    if (alignment == 0u) {
        return true;
    }

    rem = reader->pos % alignment;
    next = rem ? (reader->pos + (alignment - rem)) : reader->pos;

    if (next > reader->size) {
        set_error(reader->err, reader->err_size, "Unexpected EOF while aligning read pointer");
        return false;
    }

    reader->pos = next;
    return true;
}

static bool reader_read_bytes(RawReader *reader, void *dst, size_t size, size_t alignment) {
    if (!reader_align(reader, alignment)) {
        return false;
    }

    if (reader->pos + size > reader->size) {
        set_error(reader->err, reader->err_size, "Unexpected EOF while parsing Rev6 BIN");
        return false;
    }

    if (size > 0u) {
        memcpy(dst, reader->data + reader->pos, size);
    }
    reader->pos += size;
    return true;
}

static bool reader_read_u8(RawReader *reader, uint8_t *out_value) {
    return reader_read_bytes(reader, out_value, sizeof(*out_value), 1u);
}

static bool reader_read_i8(RawReader *reader, int8_t *out_value) {
    return reader_read_bytes(reader, out_value, sizeof(*out_value), 1u);
}

static bool reader_read_u16(RawReader *reader, uint16_t *out_value) {
    uint8_t raw[2];
    if (!reader_read_bytes(reader, raw, sizeof(raw), 2u)) {
        return false;
    }
    *out_value = (uint16_t)(raw[0] | ((uint16_t)raw[1] << 8));
    return true;
}

static bool reader_read_i16(RawReader *reader, int16_t *out_value) {
    uint16_t value;
    if (!reader_read_u16(reader, &value)) {
        return false;
    }
    *out_value = (int16_t)value;
    return true;
}

static bool reader_read_u32(RawReader *reader, uint32_t *out_value) {
    uint8_t raw[4];
    if (!reader_read_bytes(reader, raw, sizeof(raw), 4u)) {
        return false;
    }
    *out_value = (uint32_t)raw[0]
        | ((uint32_t)raw[1] << 8)
        | ((uint32_t)raw[2] << 16)
        | ((uint32_t)raw[3] << 24);
    return true;
}

static bool reader_read_i32(RawReader *reader, int32_t *out_value) {
    uint32_t value;
    if (!reader_read_u32(reader, &value)) {
        return false;
    }
    *out_value = (int32_t)value;
    return true;
}

static bool reader_read_f32(RawReader *reader, float *out_value) {
    uint32_t raw;
    union {
        uint32_t u;
        float f;
    } cvt;

    if (!reader_read_u32(reader, &raw)) {
        return false;
    }

    cvt.u = raw;
    *out_value = cvt.f;
    return true;
}

static bool reader_read_string(RawReader *reader, char **out_text) {
    uint32_t length_with_null;
    size_t payload_len;
    size_t pad;
    char *text;

    if (!reader_read_u32(reader, &length_with_null)) {
        return false;
    }

    if (length_with_null == 0u) {
        set_error(reader->err, reader->err_size, "Invalid string length 0 in Rev6 BIN");
        return false;
    }

    payload_len = (size_t)(length_with_null - 1u);
    if (reader->pos + payload_len > reader->size) {
        set_error(reader->err, reader->err_size, "String payload exceeds file bounds");
        return false;
    }

    text = (char *)malloc(payload_len + 1u);
    if (!text) {
        set_error(reader->err, reader->err_size, "Out of memory while parsing string");
        return false;
    }

    if (payload_len > 0u) {
        memcpy(text, reader->data + reader->pos, payload_len);
    }
    text[payload_len] = '\0';
    reader->pos += payload_len;

    pad = (payload_len % 4u) ? (4u - (payload_len % 4u)) : 4u;
    if (reader->pos + pad > reader->size) {
        free(text);
        set_error(reader->err, reader->err_size, "String alignment exceeds file bounds");
        return false;
    }

    reader->pos += pad;
    *out_text = text;
    return true;
}

static void normalize_path_separators(const char *src, char *dst, size_t dst_size) {
    size_t i;
    if (!dst || dst_size == 0u) {
        return;
    }
    dst[0] = '\0';
    if (!src) {
        return;
    }
    for (i = 0u; src[i] && i + 1u < dst_size; i++) {
        dst[i] = (src[i] == '\\') ? '/' : src[i];
    }
    dst[i] = '\0';
}

static bool is_absolute_path(const char *path) {
    const char *colon;

    if (!path || !path[0]) {
        return false;
    }

    if (path[0] == '/' || path[0] == '\\') {
        return true;
    }

    colon = strchr(path, ':');
    if (colon && (colon[1] == '/' || colon[1] == '\\')) {
        return true;
    }

    return false;
}

static const char *path_basename_ptr(const char *path) {
    const char *slash1;
    const char *slash2;
    const char *slash;

    if (!path) {
        return "";
    }

    slash1 = strrchr(path, '/');
    slash2 = strrchr(path, '\\');
    slash = slash1;
    if (!slash || (slash2 && slash2 > slash)) {
        slash = slash2;
    }

    return slash ? (slash + 1) : path;
}

static void path_dirname_copy(const char *path, char *out_dir, size_t out_size) {
    const char *slash1;
    const char *slash2;
    const char *slash;
    size_t len;

    if (!out_dir || out_size == 0u) {
        return;
    }

    out_dir[0] = '\0';

    if (!path || !path[0]) {
        snprintf(out_dir, out_size, ".");
        return;
    }

    slash1 = strrchr(path, '/');
    slash2 = strrchr(path, '\\');
    slash = slash1;
    if (!slash || (slash2 && slash2 > slash)) {
        slash = slash2;
    }

    if (!slash) {
        snprintf(out_dir, out_size, ".");
        return;
    }

    len = (size_t)(slash - path);
    if (len == 0u) {
        len = 1u;
    }

    if (len >= out_size) {
        len = out_size - 1u;
    }

    memcpy(out_dir, path, len);
    out_dir[len] = '\0';
}

static void path_parent_copy(const char *path, char *out_parent, size_t out_size) {
    char dir[PATH_BUF_SIZE];

    if (!out_parent || out_size == 0u) {
        return;
    }

    out_parent[0] = '\0';
    path_dirname_copy(path, dir, sizeof(dir));
    path_dirname_copy(dir, out_parent, out_size);
}
static bool path_has_extension(const char *path) {
    const char *base;
    const char *dot;

    if (!path || !path[0]) {
        return false;
    }

    base = path_basename_ptr(path);
    dot = strrchr(base, '.');
    return (dot && dot[1] != '\0');
}

static void replace_extension(const char *path, const char *new_ext, char *out, size_t out_size) {
    const char *base;
    const char *dot;
    size_t prefix_len;

    if (!out || out_size == 0u) {
        return;
    }

    out[0] = '\0';
    if (!path || !path[0]) {
        return;
    }

    base = path_basename_ptr(path);
    dot = strrchr(base, '.');

    if (dot) {
        prefix_len = (size_t)(dot - path);
    } else {
        prefix_len = strlen(path);
    }

    if (prefix_len >= out_size) {
        prefix_len = out_size - 1u;
    }

    memcpy(out, path, prefix_len);
    out[prefix_len] = '\0';
    strncat(out, new_ext, out_size - strlen(out) - 1u);
}

static void join_path(const char *base, const char *tail, char *out, size_t out_size) {
    if (!out || out_size == 0u) {
        return;
    }

    out[0] = '\0';
    if (!tail || !tail[0]) {
        return;
    }

    if (!base || !base[0]) {
        snprintf(out, out_size, "%s", tail);
        return;
    }

    if (is_absolute_path(tail)) {
        snprintf(out, out_size, "%s", tail);
        return;
    }

    if (base[strlen(base) - 1u] == '/' || base[strlen(base) - 1u] == '\\') {
        snprintf(out, out_size, "%s%s", base, tail);
    } else {
        snprintf(out, out_size, "%s/%s", base, tail);
    }
}

static bool string_vec_push(StringVec *vec, char *text, char *err, size_t err_size) {
    char **new_items;

    if (vec->count >= vec->cap) {
        uint32_t new_cap = (vec->cap == 0u) ? 4u : (vec->cap * 2u);
        new_items = (char **)realloc(vec->items, sizeof(char *) * new_cap);
        if (!new_items) {
            set_error(err, err_size, "Out of memory while growing path list");
            return false;
        }
        vec->items = new_items;
        vec->cap = new_cap;
    }

    vec->items[vec->count++] = text;
    return true;
}

static int32_t string_vec_find(const StringVec *vec, const char *text) {
    uint32_t i;

    if (!vec || !text) {
        return -1;
    }

    for (i = 0u; i < vec->count; i++) {
        if (vec->items[i] && strcmp(vec->items[i], text) == 0) {
            return (int32_t)i;
        }
    }

    return -1;
}

static void string_vec_free(StringVec *vec) {
    uint32_t i;

    if (!vec) {
        return;
    }

    for (i = 0u; i < vec->count; i++) {
        free(vec->items[i]);
        vec->items[i] = NULL;
    }

    free(vec->items);
    vec->items = NULL;
    vec->count = 0u;
    vec->cap = 0u;
}

static bool sprite_vec_push(SpriteVec *vec, Av6Sprite sprite, uint16_t *out_index, char *err, size_t err_size) {
    Av6Sprite *new_items;

    if (vec->count >= vec->cap) {
        uint32_t new_cap = (vec->cap == 0u) ? 64u : (vec->cap * 2u);
        new_items = (Av6Sprite *)realloc(vec->items, sizeof(Av6Sprite) * new_cap);
        if (!new_items) {
            set_error(err, err_size, "Out of memory while growing sprite list");
            return false;
        }
        vec->items = new_items;
        vec->cap = new_cap;
    }

    vec->items[vec->count] = sprite;
    if (out_index) {
        *out_index = (uint16_t)vec->count;
    }
    vec->count++;
    return true;
}

static void sprite_vec_free(SpriteVec *vec) {
    uint32_t i;

    if (!vec) {
        return;
    }

    for (i = 0u; i < vec->count; i++) {
        free(vec->items[i].name);
        vec->items[i].name = NULL;
    }

    free(vec->items);
    vec->items = NULL;
    vec->count = 0u;
    vec->cap = 0u;
}

static bool keyframe_vec_push(KeyframeVec *vec, Av6Keyframe keyframe, char *err, size_t err_size) {
    Av6Keyframe *new_items;

    if (vec->count >= vec->cap) {
        uint32_t new_cap = (vec->cap == 0u) ? 512u : (vec->cap * 2u);
        new_items = (Av6Keyframe *)realloc(vec->items, sizeof(Av6Keyframe) * new_cap);
        if (!new_items) {
            set_error(err, err_size, "Out of memory while growing keyframe list");
            return false;
        }
        vec->items = new_items;
        vec->cap = new_cap;
    }

    vec->items[vec->count++] = keyframe;
    return true;
}

static bool lookup_vec_push(LookupVec *vec, SpriteLookup lookup, char *err, size_t err_size) {
    SpriteLookup *new_items;

    if (vec->count >= vec->cap) {
        uint32_t new_cap = (vec->cap == 0u) ? 64u : (vec->cap * 2u);
        new_items = (SpriteLookup *)realloc(vec->items, sizeof(SpriteLookup) * new_cap);
        if (!new_items) {
            set_error(err, err_size, "Out of memory while growing sprite lookup list");
            return false;
        }
        vec->items = new_items;
        vec->cap = new_cap;
    }

    vec->items[vec->count++] = lookup;
    return true;
}

static void lookup_vec_free(LookupVec *vec) {
    uint32_t i;

    if (!vec) {
        return;
    }

    for (i = 0u; i < vec->count; i++) {
        free(vec->items[i].name);
        vec->items[i].name = NULL;
    }

    free(vec->items);
    vec->items = NULL;
    vec->count = 0u;
    vec->cap = 0u;
}

static int32_t lookup_find_exact(const LookupVec *lookup, uint16_t source_id, const char *name, bool enforce_source, bool ignore_case) {
    uint32_t i;

    if (!lookup || !name || !name[0]) {
        return -1;
    }

    for (i = 0u; i < lookup->count; i++) {
        const SpriteLookup *entry = &lookup->items[i];

        if (enforce_source && entry->source_id != source_id) {
            continue;
        }

        if (!entry->name) {
            continue;
        }

        if (ignore_case) {
            if (strcasecmp(entry->name, name) == 0) {
                return (int32_t)i;
            }
        } else {
            if (strcmp(entry->name, name) == 0) {
                return (int32_t)i;
            }
        }
    }

    return -1;
}

static uint16_t lookup_sprite_index(const LookupVec *lookup, int16_t source_id, const char *name) {
    int32_t idx;

    idx = lookup_find_exact(lookup, (uint16_t)source_id, name, true, false);
    if (idx >= 0) {
        return lookup->items[idx].sprite_index;
    }

    idx = lookup_find_exact(lookup, (uint16_t)source_id, name, true, true);
    if (idx >= 0) {
        return lookup->items[idx].sprite_index;
    }

    idx = lookup_find_exact(lookup, 0u, name, false, false);
    if (idx >= 0) {
        return lookup->items[idx].sprite_index;
    }

    idx = lookup_find_exact(lookup, 0u, name, false, true);
    if (idx >= 0) {
        return lookup->items[idx].sprite_index;
    }

    return AV6_NO_SPRITE;
}

static bool tag_name_is(const char *tag_start, const char *tag_end, const char *name) {
    const char *cursor;
    const char *name_end;
    size_t tag_len;
    size_t name_len;

    if (!tag_start || !tag_end || !name) {
        return false;
    }

    cursor = tag_start;
    while (cursor < tag_end && isspace((unsigned char)*cursor)) {
        cursor++;
    }

    name_end = cursor;
    while (name_end < tag_end && (isalnum((unsigned char)*name_end) || *name_end == '_' || *name_end == '-')) {
        name_end++;
    }

    tag_len = (size_t)(name_end - cursor);
    name_len = strlen(name);

    if (tag_len != name_len) {
        return false;
    }

    return strncasecmp(cursor, name, name_len) == 0;
}

static bool extract_attr_value(const char *tag_start, const char *tag_end, const char *attr_name, char *out, size_t out_size) {
    const char *cursor;

    if (!tag_start || !tag_end || !attr_name || !out || out_size == 0u) {
        return false;
    }

    out[0] = '\0';
    cursor = tag_start;

    while (cursor < tag_end) {
        const char *key_start;
        const char *key_end;
        size_t key_len;
        const char *value_start;
        const char *value_end;
        char quote;

        while (cursor < tag_end && !(isalpha((unsigned char)*cursor) || *cursor == '_' || *cursor == '-')) {
            cursor++;
        }
        if (cursor >= tag_end) {
            break;
        }

        key_start = cursor;
        while (cursor < tag_end && (isalnum((unsigned char)*cursor) || *cursor == '_' || *cursor == '-' || *cursor == ':')) {
            cursor++;
        }
        key_end = cursor;
        key_len = (size_t)(key_end - key_start);

        while (cursor < tag_end && isspace((unsigned char)*cursor)) {
            cursor++;
        }
        if (cursor >= tag_end || *cursor != '=') {
            continue;
        }
        cursor++;
        while (cursor < tag_end && isspace((unsigned char)*cursor)) {
            cursor++;
        }
        if (cursor >= tag_end || (*cursor != '\"' && *cursor != '\'')) {
            continue;
        }

        quote = *cursor;
        cursor++;
        value_start = cursor;
        value_end = value_start;
        while (value_end < tag_end && *value_end != quote) {
            value_end++;
        }

        if (value_end >= tag_end) {
            break;
        }

        if (strlen(attr_name) == key_len && strncasecmp(key_start, attr_name, key_len) == 0) {
            size_t copy_len = (size_t)(value_end - value_start);
            if (copy_len >= out_size) {
                copy_len = out_size - 1u;
            }
            memcpy(out, value_start, copy_len);
            out[copy_len] = '\0';
            return true;
        }

        cursor = value_end + 1;
    }

    return false;
}

static float parse_attr_float(const char *tag_start, const char *tag_end, const char *name_a, const char *name_b, float fallback) {
    char value[64];

    if (name_a && extract_attr_value(tag_start, tag_end, name_a, value, sizeof(value))) {
        char *end = NULL;
        float parsed = strtof(value, &end);
        if (end && end != value) {
            return parsed;
        }
    }

    if (name_b && extract_attr_value(tag_start, tag_end, name_b, value, sizeof(value))) {
        char *end = NULL;
        float parsed = strtof(value, &end);
        if (end && end != value) {
            return parsed;
        }
    }

    return fallback;
}

static bool attr_value_truthy(const char *value) {
    if (!value || !value[0]) {
        return false;
    }

    return (
        strcasecmp(value, "true") == 0
        || strcasecmp(value, "yes") == 0
        || strcasecmp(value, "on") == 0
        || strcmp(value, "1") == 0
        || value[0] == 'y'
        || value[0] == 'Y'
        || value[0] == 't'
        || value[0] == 'T'
    );
}

static float parse_atlas_draw_scale(const uint8_t *blob) {
    const char *cursor;

    if (!blob) {
        return 1.0f;
    }

    cursor = (const char *)blob;
    while ((cursor = strchr(cursor, '<')) != NULL) {
        const char *tag_start = cursor + 1;
        const char *tag_end = strchr(tag_start, '>');
        char hires_value[32];

        if (!tag_end) {
            break;
        }

        if (tag_start[0] == '/' || tag_start[0] == '!' || tag_start[0] == '?') {
            cursor = tag_end + 1;
            continue;
        }

        if (!tag_name_is(tag_start, tag_end, "TextureAtlas")) {
            cursor = tag_end + 1;
            continue;
        }

        hires_value[0] = '\0';
        if (extract_attr_value(tag_start, tag_end, "hires", hires_value, sizeof(hires_value))
            || extract_attr_value(tag_start, tag_end, "isHires", hires_value, sizeof(hires_value))) {
            return attr_value_truthy(hires_value) ? 0.5f : 1.0f;
        }

        return 1.0f;
    }

    return 1.0f;
}

static bool resolve_source_xml_path(const char *bin_path, const char *source_src, char *out_path, size_t out_size) {
    char normalized_src[PATH_BUF_SIZE];
    char bin_dir[PATH_BUF_SIZE];
    char data_dir[PATH_BUF_SIZE];
    char candidate[PATH_BUF_SIZE];
    char with_ext[PATH_BUF_SIZE];
    char xml_resources_tail[PATH_BUF_SIZE];
    const char *bin_dir_name;

    if (!out_path || out_size == 0u) {
        return false;
    }

    out_path[0] = '\0';

    if (!source_src || !source_src[0]) {
        return false;
    }

    normalize_path_separators(source_src, normalized_src, sizeof(normalized_src));
    path_dirname_copy(bin_path, bin_dir, sizeof(bin_dir));

    bin_dir_name = path_basename_ptr(bin_dir);
    if (strcasecmp(bin_dir_name, "xml_bin") == 0 || strcasecmp(bin_dir_name, "xml_resources") == 0) {
        path_parent_copy(bin_path, data_dir, sizeof(data_dir));
    } else {
        snprintf(data_dir, sizeof(data_dir), "%s", bin_dir);
    }

    if (is_absolute_path(normalized_src) && file_exists(normalized_src)) {
        snprintf(out_path, out_size, "%s", normalized_src);
        return true;
    }

    join_path(bin_dir, normalized_src, candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    join_path(bin_dir, path_basename_ptr(normalized_src), candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    join_path(data_dir, normalized_src, candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    join_path(data_dir, path_basename_ptr(normalized_src), candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    snprintf(xml_resources_tail, sizeof(xml_resources_tail), "xml_resources/%s", normalized_src);
    join_path(data_dir, xml_resources_tail, candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    snprintf(xml_resources_tail, sizeof(xml_resources_tail), "xml_resources/%s", path_basename_ptr(normalized_src));
    join_path(data_dir, xml_resources_tail, candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    if (!path_has_extension(normalized_src)) {
        snprintf(with_ext, sizeof(with_ext), "%s.xml", normalized_src);
        if (is_absolute_path(with_ext) && file_exists(with_ext)) {
            snprintf(out_path, out_size, "%s", with_ext);
            return true;
        }

        join_path(bin_dir, with_ext, candidate, sizeof(candidate));
        if (file_exists(candidate)) {
            snprintf(out_path, out_size, "%s", candidate);
            return true;
        }

        snprintf(with_ext, sizeof(with_ext), "%s.xml", path_basename_ptr(normalized_src));
        join_path(bin_dir, with_ext, candidate, sizeof(candidate));
        if (file_exists(candidate)) {
            snprintf(out_path, out_size, "%s", candidate);
            return true;
        }

        join_path(data_dir, with_ext, candidate, sizeof(candidate));
        if (file_exists(candidate)) {
            snprintf(out_path, out_size, "%s", candidate);
            return true;
        }

        snprintf(with_ext, sizeof(with_ext), "%s.xml", path_basename_ptr(normalized_src));
        join_path(data_dir, with_ext, candidate, sizeof(candidate));
        if (file_exists(candidate)) {
            snprintf(out_path, out_size, "%s", candidate);
            return true;
        }

        snprintf(xml_resources_tail, sizeof(xml_resources_tail), "xml_resources/%s.xml", normalized_src);
        join_path(data_dir, xml_resources_tail, candidate, sizeof(candidate));
        if (file_exists(candidate)) {
            snprintf(out_path, out_size, "%s", candidate);
            return true;
        }

        snprintf(xml_resources_tail, sizeof(xml_resources_tail), "xml_resources/%s.xml", path_basename_ptr(normalized_src));
        join_path(data_dir, xml_resources_tail, candidate, sizeof(candidate));
        if (file_exists(candidate)) {
            snprintf(out_path, out_size, "%s", candidate);
            return true;
        }
    }

    return false;
}

static bool extract_atlas_image_path(const char *xml_path, char *out_image_path, size_t out_size, char *err, size_t err_size) {
    uint8_t *blob = NULL;
    size_t blob_size = 0u;
    const char *cursor;

    if (!out_image_path || out_size == 0u) {
        return false;
    }

    out_image_path[0] = '\0';

    if (!load_file_blob(xml_path, &blob, &blob_size, err, err_size)) {
        return false;
    }


    cursor = (const char *)blob;
    while ((cursor = strchr(cursor, '<')) != NULL) {
        const char *tag_start = cursor + 1;
        const char *tag_end = strchr(tag_start, '>');

        if (!tag_end) {
            break;
        }

        if (tag_start[0] != '/' && tag_start[0] != '!' && tag_start[0] != '?') {
            if (tag_name_is(tag_start, tag_end, "TextureAtlas")) {
                if (extract_attr_value(tag_start, tag_end, "imagePath", out_image_path, out_size)) {
                    free(blob);
                    return true;
                }
            }
        }

        cursor = tag_end + 1;
    }

    free(blob);
    return false;
}

static bool resolve_t3x_path(
    const char *bin_path,
    const char *xml_path,
    const char *atlas_image_path,
    char *out_path,
    size_t out_size
) {
    char bin_dir[PATH_BUF_SIZE];
    char xml_dir[PATH_BUF_SIZE];
    char data_dir[PATH_BUF_SIZE];
    char normalized_image[PATH_BUF_SIZE];
    char rel_t3x[PATH_BUF_SIZE];
    char candidate[PATH_BUF_SIZE];
    char xml_based[PATH_BUF_SIZE];
    const char *xml_dir_name;

    if (!out_path || out_size == 0u) {
        return false;
    }

    out_path[0] = '\0';
    path_dirname_copy(bin_path, bin_dir, sizeof(bin_dir));
    path_dirname_copy(xml_path, xml_dir, sizeof(xml_dir));

    xml_dir_name = path_basename_ptr(xml_dir);
    if (strcasecmp(xml_dir_name, "xml_bin") == 0 || strcasecmp(xml_dir_name, "xml_resources") == 0) {
        path_parent_copy(xml_path, data_dir, sizeof(data_dir));
    } else {
        const char *bin_dir_name = path_basename_ptr(bin_dir);
        if (strcasecmp(bin_dir_name, "xml_bin") == 0 || strcasecmp(bin_dir_name, "xml_resources") == 0) {
            path_parent_copy(bin_path, data_dir, sizeof(data_dir));
        } else {
            snprintf(data_dir, sizeof(data_dir), "%s", xml_dir);
        }
    }

    if (atlas_image_path && atlas_image_path[0]) {
        normalize_path_separators(atlas_image_path, normalized_image, sizeof(normalized_image));
        replace_extension(normalized_image, ".t3x", rel_t3x, sizeof(rel_t3x));
    } else {
        normalize_path_separators(path_basename_ptr(xml_path), normalized_image, sizeof(normalized_image));
        replace_extension(normalized_image, ".t3x", rel_t3x, sizeof(rel_t3x));
    }

    if (is_absolute_path(rel_t3x) && file_exists(rel_t3x)) {
        snprintf(out_path, out_size, "%s", rel_t3x);
        return true;
    }

    join_path(xml_dir, rel_t3x, candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    join_path(xml_dir, path_basename_ptr(rel_t3x), candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    join_path(data_dir, rel_t3x, candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    join_path(data_dir, path_basename_ptr(rel_t3x), candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    join_path(bin_dir, rel_t3x, candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    join_path(bin_dir, path_basename_ptr(rel_t3x), candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    snprintf(candidate, sizeof(candidate), "gfx/monsters/%s", path_basename_ptr(rel_t3x));
    join_path(data_dir, candidate, xml_based, sizeof(xml_based));
    if (file_exists(xml_based)) {
        snprintf(out_path, out_size, "%s", xml_based);
        return true;
    }

    normalize_path_separators(path_basename_ptr(xml_path), normalized_image, sizeof(normalized_image));
    replace_extension(normalized_image, ".t3x", xml_based, sizeof(xml_based));
    join_path(xml_dir, xml_based, candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    join_path(data_dir, xml_based, candidate, sizeof(candidate));
    if (file_exists(candidate)) {
        snprintf(out_path, out_size, "%s", candidate);
        return true;
    }

    return false;
}
static bool parse_atlas_sprites(
    const char *xml_path,
    uint16_t source_id,
    uint16_t sheet_index,
    SpriteVec *sprites,
    LookupVec *lookup,
    char *err,
    size_t err_size
) {
    uint8_t *blob = NULL;
    size_t blob_size = 0u;
    const char *cursor;
    uint32_t parsed_count = 0u;
    float sheet_draw_scale = 1.0f;

    if (!load_file_blob(xml_path, &blob, &blob_size, err, err_size)) {
        return false;
    }

    sheet_draw_scale = parse_atlas_draw_scale(blob);

    cursor = (const char *)blob;
    while ((cursor = strchr(cursor, '<')) != NULL) {
        const char *tag_start = cursor + 1;
        const char *tag_end = strchr(tag_start, '>');

        if (!tag_end) {
            break;
        }

        if (tag_start[0] == '/' || tag_start[0] == '!' || tag_start[0] == '?') {
            cursor = tag_end + 1;
            continue;
        }

        if (tag_name_is(tag_start, tag_end, "sprite") || tag_name_is(tag_start, tag_end, "SubTexture")) {
            char name[256];
            Av6Sprite sprite;
            uint16_t sprite_index;
            SpriteLookup lookup_entry;

            name[0] = '\0';
            if (!extract_attr_value(tag_start, tag_end, "name", name, sizeof(name))) {
                extract_attr_value(tag_start, tag_end, "n", name, sizeof(name));
            }
            if (!name[0]) {
                cursor = tag_end + 1;
                continue;
            }

            if (lookup_find_exact(lookup, source_id, name, true, false) >= 0) {
                cursor = tag_end + 1;
                continue;
            }

            memset(&sprite, 0, sizeof(sprite));
            sprite.name = dup_text(name);
            if (!sprite.name) {
                free(blob);
                set_error(err, err_size, "Out of memory while parsing atlas sprite names");
                return false;
            }

            sprite.x = parse_attr_float(tag_start, tag_end, "x", NULL, 0.0f);
            sprite.y = parse_attr_float(tag_start, tag_end, "y", NULL, 0.0f);
            sprite.w = parse_attr_float(tag_start, tag_end, "w", "width", 0.0f);
            sprite.h = parse_attr_float(tag_start, tag_end, "h", "height", 0.0f);
            sprite.frame_x = parse_attr_float(tag_start, tag_end, "oX", "frameX", 0.0f);
            sprite.frame_y = parse_attr_float(tag_start, tag_end, "oY", "frameY", 0.0f);
            sprite.frame_w = parse_attr_float(tag_start, tag_end, "oW", "frameWidth", sprite.w);
            sprite.frame_h = parse_attr_float(tag_start, tag_end, "oH", "frameHeight", sprite.h);
            sprite.draw_scale = sheet_draw_scale;
            sprite.sheet_index = sheet_index;
            sprite.rotated = 0u;

            {
                char rotated_value[32];
                rotated_value[0] = '\0';
                if (extract_attr_value(tag_start, tag_end, "r", rotated_value, sizeof(rotated_value))
                    || extract_attr_value(tag_start, tag_end, "rotated", rotated_value, sizeof(rotated_value))) {
                    if (attr_value_truthy(rotated_value)) {
                        sprite.rotated = 1u;
                    }
                }
            }

            if (sprite.frame_w <= 0.0f) {
                sprite.frame_w = sprite.w;
            }
            if (sprite.frame_h <= 0.0f) {
                sprite.frame_h = sprite.h;
            }

            if (!sprite_vec_push(sprites, sprite, &sprite_index, err, err_size)) {
                free(sprite.name);
                free(blob);
                return false;
            }

            lookup_entry.source_id = source_id;
            lookup_entry.sprite_index = sprite_index;
            lookup_entry.name = dup_text(name);
            if (!lookup_entry.name) {
                free(blob);
                set_error(err, err_size, "Out of memory while building sprite lookup");
                return false;
            }

            if (!lookup_vec_push(lookup, lookup_entry, err, err_size)) {
                free(lookup_entry.name);
                free(blob);
                return false;
            }

            parsed_count++;
        }

        cursor = tag_end + 1;
    }

    free(blob);

    if (parsed_count == 0u) {
        set_error(err, err_size, "No sprite entries found in atlas: %s", xml_path);
        return false;
    }

    return true;
}

static bool parse_sources_and_atlases(
    const char *bin_path,
    RawReader *reader,
    RawSourceList *sources,
    SpriteVec *sprites,
    LookupVec *lookup,
    StringVec *sheet_paths,
    char *err,
    size_t err_size
) {
    uint32_t source_count;
    uint32_t i;

    if (!reader_read_u32(reader, &source_count)) {
        return false;
    }

    if (source_count > RAW_MAX_SOURCES) {
        set_error(err, err_size, "Source count too large for Rev6 parser: %lu", (unsigned long)source_count);
        return false;
    }

    if (source_count == 0u) {
        sources->items = NULL;
        sources->count = 0u;
        return true;
    }

    sources->items = (RawSource *)calloc(source_count, sizeof(RawSource));
    if (!sources->items) {
        set_error(err, err_size, "Out of memory while allocating source table");
        return false;
    }
    sources->count = source_count;

    for (i = 0u; i < source_count; i++) {
        char *src = NULL;
        uint16_t source_id;
        uint16_t width;
        uint16_t height;
        char xml_path[PATH_BUF_SIZE];
        char atlas_image[PATH_BUF_SIZE];
        char t3x_path[PATH_BUF_SIZE];
        uint16_t sheet_index = 0xFFFFu;

        if (!reader_read_string(reader, &src)
            || !reader_read_u16(reader, &source_id)
            || !reader_read_u16(reader, &width)
            || !reader_read_u16(reader, &height)) {
            free(src);
            return false;
        }

        (void)width;
        (void)height;

        sources->items[i].src = src;
        sources->items[i].id = source_id;

        xml_path[0] = '\0';
        if (!resolve_source_xml_path(bin_path, src, xml_path, sizeof(xml_path))) {
            continue;
        }

        atlas_image[0] = '\0';
        extract_atlas_image_path(xml_path, atlas_image, sizeof(atlas_image), err, err_size);

        t3x_path[0] = '\0';
        if (resolve_t3x_path(bin_path, xml_path, atlas_image, t3x_path, sizeof(t3x_path))) {
            int32_t existing = string_vec_find(sheet_paths, t3x_path);
            if (existing >= 0) {
                sheet_index = (uint16_t)existing;
            } else {
                char *path_copy = dup_text(t3x_path);
                if (!path_copy) {
                    set_error(err, err_size, "Out of memory while storing sheet paths");
                    return false;
                }
                if (!string_vec_push(sheet_paths, path_copy, err, err_size)) {
                    free(path_copy);
                    return false;
                }
                sheet_index = (uint16_t)(sheet_paths->count - 1u);
            }
        }

        if (!parse_atlas_sprites(xml_path, source_id, sheet_index, sprites, lookup, err, err_size)) {
            /* Non-fatal: keep BIN parsing alive even if an atlas is malformed. */
            if (err && strstr(err, "Out of memory") != NULL) {
                return false;
            }
            if (err && err_size > 0u) {
                err[0] = '\0';
            }
        }
    }

    return true;
}

static void free_source_list(RawSourceList *sources) {
    uint32_t i;

    if (!sources) {
        return;
    }

    for (i = 0u; i < sources->count; i++) {
        free(sources->items[i].src);
        sources->items[i].src = NULL;
    }

    free(sources->items);
    sources->items = NULL;
    sources->count = 0u;
}

static bool skip_animation_block(RawReader *reader, char *err, size_t err_size) {
    char *anim_name = NULL;
    uint16_t width;
    uint16_t height;
    float loop_offset;
    uint32_t centered;
    uint32_t layer_count;
    uint32_t layer_idx;

    if (!reader_read_string(reader, &anim_name)
        || !reader_read_u16(reader, &width)
        || !reader_read_u16(reader, &height)
        || !reader_read_f32(reader, &loop_offset)
        || !reader_read_u32(reader, &centered)
        || !reader_read_u32(reader, &layer_count)) {
        free(anim_name);
        return false;
    }

    free(anim_name);
    (void)width;
    (void)height;
    (void)loop_offset;
    (void)centered;

    if (layer_count > RAW_MAX_LAYERS) {
        set_error(err, err_size, "Layer count too large for Rev6 parser: %lu", (unsigned long)layer_count);
        return false;
    }

    for (layer_idx = 0u; layer_idx < layer_count; layer_idx++) {
        char *layer_name = NULL;
        char *unk = NULL;
        int32_t layer_type;
        uint32_t blend;
        int16_t parent_id;
        int16_t layer_id;
        int16_t source_id;
        uint16_t layer_width;
        uint16_t layer_height;
        float layer_anchor_x;
        float layer_anchor_y;
        uint32_t frame_count;
        uint32_t frame_idx;

        if (!reader_read_string(reader, &layer_name)
            || !reader_read_i32(reader, &layer_type)
            || !reader_read_u32(reader, &blend)
            || !reader_read_i16(reader, &parent_id)
            || !reader_read_i16(reader, &layer_id)
            || !reader_read_i16(reader, &source_id)
            || !reader_read_u16(reader, &layer_width)
            || !reader_read_u16(reader, &layer_height)
            || !reader_read_f32(reader, &layer_anchor_x)
            || !reader_read_f32(reader, &layer_anchor_y)
            || !reader_read_string(reader, &unk)
            || !reader_read_u32(reader, &frame_count)) {
            free(layer_name);
            free(unk);
            return false;
        }

        free(layer_name);
        free(unk);
        (void)layer_type;
        (void)blend;
        (void)parent_id;
        (void)layer_id;
        (void)source_id;
        (void)layer_width;
        (void)layer_height;
        (void)layer_anchor_x;
        (void)layer_anchor_y;

        if (frame_count > RAW_MAX_FRAMES_PER_LAYER) {
            set_error(err, err_size, "Frame count too large while skipping animation: %lu", (unsigned long)frame_count);
            return false;
        }

        for (frame_idx = 0u; frame_idx < frame_count; frame_idx++) {
            float time;
            int8_t immediate_pos;
            float x;
            float y;
            int8_t immediate_scale;
            float scale_x;
            float scale_y;
            int8_t immediate_rotation;
            float rotation;
            int8_t immediate_opacity;
            float opacity;
            int8_t immediate_sprite;
            char *sprite_name = NULL;
            int8_t immediate_rgb;
            uint8_t r;
            uint8_t g;
            uint8_t b;

            if (!reader_read_f32(reader, &time)
                || !reader_read_i8(reader, &immediate_pos)
                || !reader_read_f32(reader, &x)
                || !reader_read_f32(reader, &y)
                || !reader_read_i8(reader, &immediate_scale)
                || !reader_read_f32(reader, &scale_x)
                || !reader_read_f32(reader, &scale_y)
                || !reader_read_i8(reader, &immediate_rotation)
                || !reader_read_f32(reader, &rotation)
                || !reader_read_i8(reader, &immediate_opacity)
                || !reader_read_f32(reader, &opacity)
                || !reader_read_i8(reader, &immediate_sprite)
                || !reader_read_string(reader, &sprite_name)
                || !reader_read_i8(reader, &immediate_rgb)
                || !reader_read_u8(reader, &r)
                || !reader_read_u8(reader, &g)
                || !reader_read_u8(reader, &b)) {
                free(sprite_name);
                return false;
            }

            free(sprite_name);
            (void)time;
            (void)immediate_pos;
            (void)x;
            (void)y;
            (void)immediate_scale;
            (void)scale_x;
            (void)scale_y;
            (void)immediate_rotation;
            (void)rotation;
            (void)immediate_opacity;
            (void)opacity;
            (void)immediate_sprite;
            (void)immediate_rgb;
            (void)r;
            (void)g;
            (void)b;
        }
    }

    return true;
}

static bool parse_single_animation(
    RawReader *reader,
    const LookupVec *lookup,
    Av6Package *pkg,
    char *out_anim_name,
    size_t out_anim_name_size,
    char *err,
    size_t err_size
) {
    char *anim_name = NULL;
    uint16_t width;
    uint16_t height;
    uint32_t centered;
    uint32_t layer_count;
    uint32_t layer_idx;
    KeyframeVec keyframes;
    float duration = 0.0f;

    memset(&keyframes, 0, sizeof(keyframes));

    if (!reader_read_string(reader, &anim_name)
        || !reader_read_u16(reader, &width)
        || !reader_read_u16(reader, &height)
        || !reader_read_f32(reader, &pkg->loop_offset)
        || !reader_read_u32(reader, &centered)
        || !reader_read_u32(reader, &layer_count)) {
        free(anim_name);
        return false;
    }

    if (out_anim_name && out_anim_name_size > 0u) {
        if (anim_name && anim_name[0]) {
            snprintf(out_anim_name, out_anim_name_size, "%s", anim_name);
        } else {
            out_anim_name[0] = 0;
        }
    }

    free(anim_name);
    pkg->centered = centered ? 1u : 0u;

    if (layer_count > RAW_MAX_LAYERS) {
        set_error(err, err_size, "Layer count too large for Rev6 parser: %lu", (unsigned long)layer_count);
        return false;
    }

    pkg->anim_width = (float)width;
    pkg->anim_height = (float)height;
    pkg->layer_count = layer_count;

    pkg->layers = (Av6Layer *)calloc(layer_count, sizeof(Av6Layer));
    if (!pkg->layers && layer_count > 0u) {
        set_error(err, err_size, "Out of memory while allocating layer table");
        return false;
    }

    for (layer_idx = 0u; layer_idx < layer_count; layer_idx++) {
        Av6Layer *layer = &pkg->layers[layer_idx];
        int32_t layer_type;
        int16_t parent_id;
        int16_t layer_id;
        int16_t source_id;
        uint16_t layer_width;
        uint16_t layer_height;
        float layer_anchor_x;
        float layer_anchor_y;
        uint32_t frame_count;
        uint32_t frame_idx;
        char *unk = NULL;

        if (!reader_read_string(reader, &layer->name)
            || !reader_read_i32(reader, &layer_type)
            || !reader_read_u32(reader, &layer->blend)
            || !reader_read_i16(reader, &parent_id)
            || !reader_read_i16(reader, &layer_id)
            || !reader_read_i16(reader, &source_id)
            || !reader_read_u16(reader, &layer_width)
            || !reader_read_u16(reader, &layer_height)
            || !reader_read_f32(reader, &layer_anchor_x)
            || !reader_read_f32(reader, &layer_anchor_y)
            || !reader_read_string(reader, &unk)
            || !reader_read_u32(reader, &frame_count)) {
            free(unk);
            return false;
        }

        free(unk);
        (void)layer_type;

        layer->id = layer_id;
        layer->parent_id = parent_id;
        layer->anchor_x = layer_anchor_x;
        layer->anchor_y = layer_anchor_y;
        layer->first_keyframe = keyframes.count;

        (void)layer_width;
        (void)layer_height;

        if (frame_count > RAW_MAX_FRAMES_PER_LAYER) {
            set_error(err, err_size, "Frame count too large in layer '%s': %lu", layer->name, (unsigned long)frame_count);
            return false;
        }

        {
            float prev_x = 0.0f;
            float prev_y = 0.0f;
            float prev_scale_x = 100.0f;
            float prev_scale_y = 100.0f;
            float prev_rotation = 0.0f;
            float prev_opacity = 100.0f;
            uint16_t prev_sprite_index = AV6_NO_SPRITE;
            uint8_t prev_r = 255u;
            uint8_t prev_g = 255u;
            uint8_t prev_b = 255u;
            bool have_prev = false;

            for (frame_idx = 0u; frame_idx < frame_count; frame_idx++) {
                Av6Keyframe keyframe;
                int8_t immediate_pos;
                int8_t immediate_scale;
                int8_t immediate_rotation;
                int8_t immediate_opacity;
                int8_t immediate_sprite;
                int8_t immediate_rgb;
                float parsed_x;
                float parsed_y;
                float parsed_scale_x;
                float parsed_scale_y;
                float parsed_rotation;
                float parsed_opacity;
                char *sprite_name = NULL;
                uint8_t r;
                uint8_t g;
                uint8_t b;

                memset(&keyframe, 0, sizeof(keyframe));
                keyframe.layer_index = layer_idx;

                if (!reader_read_f32(reader, &keyframe.time)
                    || !reader_read_i8(reader, &immediate_pos)
                    || !reader_read_f32(reader, &parsed_x)
                    || !reader_read_f32(reader, &parsed_y)
                    || !reader_read_i8(reader, &immediate_scale)
                    || !reader_read_f32(reader, &parsed_scale_x)
                    || !reader_read_f32(reader, &parsed_scale_y)
                    || !reader_read_i8(reader, &immediate_rotation)
                    || !reader_read_f32(reader, &parsed_rotation)
                    || !reader_read_i8(reader, &immediate_opacity)
                    || !reader_read_f32(reader, &parsed_opacity)
                    || !reader_read_i8(reader, &immediate_sprite)
                    || !reader_read_string(reader, &sprite_name)
                    || !reader_read_i8(reader, &immediate_rgb)
                    || !reader_read_u8(reader, &r)
                    || !reader_read_u8(reader, &g)
                    || !reader_read_u8(reader, &b)) {
                    free(sprite_name);
                    return false;
                }

                keyframe.immediate_pos = immediate_pos;
                keyframe.immediate_scale = immediate_scale;
                keyframe.immediate_rotation = immediate_rotation;
                keyframe.immediate_opacity = immediate_opacity;
                keyframe.immediate_sprite = immediate_sprite;
                keyframe.immediate_rgb = immediate_rgb;

                if (immediate_pos == -1) {
                    keyframe.x = have_prev ? prev_x : 0.0f;
                    keyframe.y = have_prev ? prev_y : 0.0f;
                } else {
                    keyframe.x = parsed_x;
                    keyframe.y = parsed_y;
                }

                if (immediate_scale == -1) {
                    keyframe.scale_x = have_prev ? prev_scale_x : 100.0f;
                    keyframe.scale_y = have_prev ? prev_scale_y : 100.0f;
                } else {
                    keyframe.scale_x = parsed_scale_x;
                    keyframe.scale_y = parsed_scale_y;
                }

                if (immediate_rotation == -1) {
                    keyframe.rotation = have_prev ? prev_rotation : 0.0f;
                } else {
                    keyframe.rotation = parsed_rotation;
                }

                if (immediate_opacity == -1) {
                    keyframe.opacity = have_prev ? prev_opacity : 100.0f;
                } else {
                    keyframe.opacity = parsed_opacity;
                }

                if (immediate_sprite == -1) {
                    keyframe.sprite_index = have_prev ? prev_sprite_index : AV6_NO_SPRITE;
                } else if (sprite_name && sprite_name[0]) {
                    keyframe.sprite_index = lookup_sprite_index(lookup, source_id, sprite_name);
                } else {
                    keyframe.sprite_index = AV6_NO_SPRITE;
                }

                if (immediate_rgb == -1) {
                    keyframe.r = have_prev ? prev_r : 255u;
                    keyframe.g = have_prev ? prev_g : 255u;
                    keyframe.b = have_prev ? prev_b : 255u;
                } else {
                    keyframe.r = r;
                    keyframe.g = g;
                    keyframe.b = b;
                }
                keyframe.a = 255u;

                free(sprite_name);

                prev_x = keyframe.x;
                prev_y = keyframe.y;
                prev_scale_x = keyframe.scale_x;
                prev_scale_y = keyframe.scale_y;
                prev_rotation = keyframe.rotation;
                prev_opacity = keyframe.opacity;
                prev_sprite_index = keyframe.sprite_index;
                prev_r = keyframe.r;
                prev_g = keyframe.g;
                prev_b = keyframe.b;
                have_prev = true;

                if (keyframe.time > duration) {
                    duration = keyframe.time;
                }

                if (!keyframe_vec_push(&keyframes, keyframe, err, err_size)) {
                    return false;
                }
            }
        }

        layer->keyframe_count = keyframes.count - layer->first_keyframe;
    }

    pkg->keyframes = keyframes.items;
    pkg->keyframe_count = keyframes.count;
    pkg->duration = duration;

    return true;
}

static bool parse_animation_at_index(
    RawReader *reader,
    const LookupVec *lookup,
    Av6Package *pkg,
    uint32_t animation_index,
    uint32_t *out_animation_count,
    uint32_t *out_selected_animation_index,
    char *out_animation_name,
    size_t out_animation_name_size,
    char *err,
    size_t err_size
) {
    uint32_t anim_count;
    uint32_t selected_index;
    uint32_t i;

    if (out_animation_count) {
        *out_animation_count = 0u;
    }
    if (out_selected_animation_index) {
        *out_selected_animation_index = 0u;
    }
    if (out_animation_name && out_animation_name_size > 0u) {
        out_animation_name[0] = 0;
    }

    if (!reader_read_u32(reader, &anim_count)) {
        return false;
    }

    if (anim_count == 0u || anim_count > RAW_MAX_ANIMS) {
        set_error(err, err_size, "Invalid animation count in Rev6 BIN: %lu", (unsigned long)anim_count);
        return false;
    }

    selected_index = (animation_index < anim_count) ? animation_index : 0u;

    if (out_animation_count) {
        *out_animation_count = anim_count;
    }
    if (out_selected_animation_index) {
        *out_selected_animation_index = selected_index;
    }

    for (i = 0u; i < anim_count; i++) {
        if (i == selected_index) {
            return parse_single_animation(
                reader,
                lookup,
                pkg,
                out_animation_name,
                out_animation_name_size,
                err,
                err_size
            );
        }
        if (!skip_animation_block(reader, err, err_size)) {
            return false;
        }
    }

    set_error(err, err_size, "Failed to resolve selected animation index %lu", (unsigned long)selected_index);
    return false;
}

bool raw_rev6_load_from_bin_with_animation(
    const char *bin_path,
    uint32_t animation_index,
    Av6Package *out_pkg,
    uint32_t *out_animation_count,
    uint32_t *out_selected_animation_index,
    char *out_animation_name,
    size_t out_animation_name_size,
    char *err,
    size_t err_size
) {
    uint8_t *bin_blob = NULL;
    size_t bin_size = 0u;
    RawReader reader;
    RawSourceList sources;
    SpriteVec sprites;
    LookupVec lookup;
    StringVec sheet_paths;
    Av6Package pkg;
    bool ok;

    if (!out_pkg) {
        return false;
    }

    memset(out_pkg, 0, sizeof(*out_pkg));
    memset(&pkg, 0, sizeof(pkg));
    memset(&sources, 0, sizeof(sources));
    memset(&sprites, 0, sizeof(sprites));
    memset(&lookup, 0, sizeof(lookup));
    memset(&sheet_paths, 0, sizeof(sheet_paths));

    if (out_animation_count) {
        *out_animation_count = 0u;
    }
    if (out_selected_animation_index) {
        *out_selected_animation_index = 0u;
    }
    if (out_animation_name && out_animation_name_size > 0u) {
        out_animation_name[0] = 0;
    }

    if (err && err_size > 0u) {
        err[0] = 0;
    }

    if (!load_file_blob(bin_path, &bin_blob, &bin_size, err, err_size)) {
        return false;
    }

    reader.data = bin_blob;
    reader.size = bin_size;
    reader.pos = 0u;
    reader.err = err;
    reader.err_size = err_size;

    ok = parse_sources_and_atlases(bin_path, &reader, &sources, &sprites, &lookup, &sheet_paths, err, err_size);
    if (!ok) {
        goto fail;
    }

    pkg.version = 6u;
    pkg.texture_blob_size = 0u;
    pkg.texture_blob = NULL;
    pkg.centered = 1u;
    pkg.external_sheet_count = sheet_paths.count;
    pkg.external_sheet_paths = sheet_paths.items;
    sheet_paths.items = NULL;
    sheet_paths.count = 0u;
    sheet_paths.cap = 0u;

    pkg.sprites = sprites.items;
    pkg.sprite_count = sprites.count;
    sprites.items = NULL;
    sprites.count = 0u;
    sprites.cap = 0u;

    if (sources.count > 0u && sources.items[0].src) {
        pkg.sheet_name = dup_text(path_basename_ptr(sources.items[0].src));
    }
    if (!pkg.sheet_name) {
        pkg.sheet_name = dup_text(path_basename_ptr(bin_path));
    }
    if (!pkg.sheet_name) {
        set_error(err, err_size, "Out of memory while storing sheet label");
        goto fail;
    }

    ok = parse_animation_at_index(
        &reader,
        &lookup,
        &pkg,
        animation_index,
        out_animation_count,
        out_selected_animation_index,
        out_animation_name,
        out_animation_name_size,
        err,
        err_size
    );
    if (!ok) {
        goto fail;
    }

    free_source_list(&sources);
    lookup_vec_free(&lookup);
    free(bin_blob);

    *out_pkg = pkg;
    return true;

fail:
    free_source_list(&sources);
    sprite_vec_free(&sprites);
    lookup_vec_free(&lookup);
    string_vec_free(&sheet_paths);
    av6_free_package(&pkg);
    free(bin_blob);
    return false;
}

bool raw_rev6_load_from_bin(const char *bin_path, Av6Package *out_pkg, char *err, size_t err_size) {
    return raw_rev6_load_from_bin_with_animation(
        bin_path,
        0u,
        out_pkg,
        NULL,
        NULL,
        NULL,
        0u,
        err,
        err_size
    );
}

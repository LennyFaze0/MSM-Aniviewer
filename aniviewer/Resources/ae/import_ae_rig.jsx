/*
AE rig import script for AniViewer exports.
Reads ae_manifest.json and reconstructs comps/layers to match the viewer viewport.
*/

(function () {
    function safeName(value) {
        var text = value ? String(value) : "animation";
        return text.replace(/[^0-9A-Za-z_-]+/g, "_").replace(/^_+|_+$/g, "") || "animation";
    }

    function readTextFile(file) {
        if (!file.exists) {
            return null;
        }
        file.open("r");
        var text = file.read();
        file.close();
        return text;
    }

    function readJSON(file) {
        var text = readTextFile(file);
        if (!text) {
            return null;
        }
        if (typeof JSON !== "undefined" && JSON.parse) {
            return JSON.parse(text);
        }
        /* fallback */
        return eval("(" + text + ")");
    }

    function toNumber(value, fallback) {
        var num = parseFloat(value);
        return isNaN(num) ? fallback : num;
    }

    function joinPath(root, rel) {
        if (!rel) {
            return root.fsName;
        }
        var normalized = String(rel).replace(/\\/g, "/");
        return root.fsName + "/" + normalized;
    }

    function formatFrameName(pattern, frameIndex) {
        var match = /%0(\d+)d/.exec(pattern);
        if (match) {
            var width = parseInt(match[1], 10);
            var padded = ("0000000000" + frameIndex).slice(-width);
            return pattern.replace(match[0], padded);
        }
        return pattern.replace("%d", String(frameIndex));
    }

    function importFootage(filePath, targetFolder, fps, isSequence) {
        var file = new File(filePath);
        if (!file.exists) {
            return null;
        }
        var options = new ImportOptions(file);
        if (isSequence) {
            options.sequence = true;
            options.forceAlphabetical = true;
        }
        var item = app.project.importFile(options);
        if (targetFolder) {
            item.parentFolder = targetFolder;
        }
        try {
            if (item.mainSource && item.mainSource.conformFrameRate) {
                item.mainSource.conformFrameRate = fps;
            }
            if (item.mainSource && item.mainSource.alphaMode !== undefined && typeof AlphaMode !== "undefined") {
                item.mainSource.alphaMode = AlphaMode.STRAIGHT;
                if (item.mainSource.premulColor !== undefined) {
                    item.mainSource.premulColor = [0, 0, 0];
                }
            }
        } catch (err) {
            /* ignore */
        }
        return item;
    }

    function applySpatialLinear(prop) {
        if (!prop || !prop.isSpatial) {
            return;
        }
        var dims = prop.value ? prop.value.length : 2;
        var zero = [];
        for (var i = 0; i < dims; i++) {
            zero.push(0);
        }
        for (var k = 1; k <= prop.numKeys; k++) {
            try {
                if (prop.setSpatialAutoBezierAtKey) {
                    prop.setSpatialAutoBezierAtKey(k, false);
                }
                if (prop.setSpatialContinuousAtKey) {
                    prop.setSpatialContinuousAtKey(k, false);
                }
                if (prop.setSpatialTangentsAtKey) {
                    prop.setSpatialTangentsAtKey(k, zero, zero);
                }
            } catch (err) {
                /* ignore */
            }
        }
    }

    function applyKeyframes(prop, keys, holdAware) {
        if (!keys || keys.length === 0) {
            return;
        }
        for (var i = 0; i < keys.length; i++) {
            var entry = keys[i];
            var value = entry.value;
            if (value && value.length === 2) {
                value = [toNumber(value[0], 0), toNumber(value[1], 0)];
            } else if (value && value.length === 3) {
                value = [toNumber(value[0], 0), toNumber(value[1], 0), toNumber(value[2], 0)];
            } else {
                value = toNumber(value, 0);
            }
            prop.setValueAtTime(toNumber(entry.time, 0), value);
        }
        if (!holdAware) {
            return;
        }
        for (var k = 1; k <= prop.numKeys; k++) {
            var data = keys[k - 1];
            var hold = data && data.hold;
            var interp = hold ? KeyframeInterpolationType.HOLD : KeyframeInterpolationType.LINEAR;
            prop.setInterpolationTypeAtKey(k, interp, interp);
        }
        applySpatialLinear(prop);
    }

    function applySampleKeys(prop, samples, valueFn) {
        if (!samples || samples.length === 0) {
            return;
        }
        for (var i = 0; i < samples.length; i++) {
            var sample = samples[i];
            prop.setValueAtTime(toNumber(sample.time, 0), valueFn(sample));
        }
        for (var k = 1; k <= prop.numKeys; k++) {
            prop.setInterpolationTypeAtKey(k, KeyframeInterpolationType.LINEAR, KeyframeInterpolationType.LINEAR);
        }
        applySpatialLinear(prop);
    }

    function applySpriteOpacityKeys(spriteLayer, spriteKeys, spriteId) {
        if (!spriteKeys || spriteKeys.length === 0) {
            spriteLayer.property("Opacity").setValue(0);
            return;
        }
        var prop = spriteLayer.property("Opacity");
        for (var i = 0; i < spriteKeys.length; i++) {
            var key = spriteKeys[i];
            var value = (key.sprite_id === spriteId) ? 100 : 0;
            prop.setValueAtTime(toNumber(key.time, 0), value);
        }
        for (var k = 1; k <= prop.numKeys; k++) {
            prop.setInterpolationTypeAtKey(k, KeyframeInterpolationType.HOLD, KeyframeInterpolationType.HOLD);
        }
    }

    function buildMeshSpriteLayers(precomp, spriteId, footage, meta, spriteEntry, duration) {
        var mesh = spriteEntry.mesh || {};
        var triangles = mesh.triangles || [];
        if (!triangles.length) {
            return [];
        }
        var layers = [];
        for (var i = 0; i < triangles.length; i++) {
            var tri = triangles[i];
            if (!tri || !tri.src || !tri.corners || tri.src.length < 3 || tri.corners.length < 4) {
                continue;
            }
            var layer = precomp.layers.add(footage);
            layer.name = (meta.name || spriteId) + "_tri_" + (i + 1);
            layer.property("Anchor Point").setValue([0, 0]);
            layer.property("Position").setValue([0, 0]);
            layer.property("Scale").setValue([100, 100]);
            layer.property("Rotation").setValue(0);
            layer.property("Opacity").setValue(100);
            layer.startTime = 0;
            layer.inPoint = 0;
            layer.outPoint = duration;

            try {
                var mask = layer.Masks.addProperty("ADBE Mask Atom");
                if (mask) {
                    mask.maskMode = MaskMode.ADD;
                    var shape = new Shape();
                    shape.vertices = [
                        [toNumber(tri.src[0][0], 0), toNumber(tri.src[0][1], 0)],
                        [toNumber(tri.src[1][0], 0), toNumber(tri.src[1][1], 0)],
                        [toNumber(tri.src[2][0], 0), toNumber(tri.src[2][1], 0)]
                    ];
                    shape.inTangents = [[0, 0], [0, 0], [0, 0]];
                    shape.outTangents = [[0, 0], [0, 0], [0, 0]];
                    shape.closed = true;
                    mask.property("ADBE Mask Shape").setValue(shape);
                }
            } catch (err) {
                /* ignore */
            }

            try {
                var effect = layer.Effects.addProperty("ADBE Corner Pin");
                if (effect) {
                    var corners = tri.corners;
                    effect.property("Upper Left").setValue([
                        toNumber(corners[0][0], 0),
                        toNumber(corners[0][1], 0)
                    ]);
                    effect.property("Upper Right").setValue([
                        toNumber(corners[1][0], 0),
                        toNumber(corners[1][1], 0)
                    ]);
                    effect.property("Lower Left").setValue([
                        toNumber(corners[2][0], 0),
                        toNumber(corners[2][1], 0)
                    ]);
                    effect.property("Lower Right").setValue([
                        toNumber(corners[3][0], 0),
                        toNumber(corners[3][1], 0)
                    ]);
                }
            } catch (err) {
                /* ignore */
            }
            layers.push(layer);
        }
        return layers;
    }

    function resolveBlendMode(value) {
        switch (value) {
            case 2:
                return BlendingMode.ADD;
            case 6:
                return BlendingMode.MULTIPLY;
            case 7:
                return BlendingMode.SCREEN;
            default:
                return BlendingMode.NORMAL;
        }
    }

    function createNull(comp, name, position, scale) {
        var layer = comp.layers.addNull();
        layer.name = name;
        layer.property("Anchor Point").setValue([0, 0]);
        layer.property("Position").setValue(position);
        layer.property("Scale").setValue(scale);
        layer.property("Rotation").setValue(0);
        return layer;
    }

    function applyWorldOffsetToRoots(comp, worldNull, offset) {
        if (!offset) {
            return;
        }
        if (Math.abs(offset[0]) < 0.0001 && Math.abs(offset[1]) < 0.0001) {
            return;
        }
        for (var i = 1; i <= comp.numLayers; i++) {
            var layer = comp.layer(i);
            if (!layer || layer === worldNull) {
                continue;
            }
            if (layer.parent !== worldNull) {
                continue;
            }
            var posProp = layer.property("Position");
            if (!posProp) {
                continue;
            }
            if (posProp.numKeys > 0) {
                for (var k = 1; k <= posProp.numKeys; k++) {
                    var v = posProp.keyValue(k);
                    var nv = [v[0] + offset[0], v[1] + offset[1]];
                    if (v.length > 2) {
                        nv.push(v[2]);
                    }
                    posProp.setValueAtKey(k, nv);
                }
            } else {
                var value = posProp.value;
                var nextValue = [value[0] + offset[0], value[1] + offset[1]];
                if (value.length > 2) {
                    nextValue.push(value[2]);
                }
                posProp.setValue(nextValue);
            }
        }
    }

    function buildLayerPrecomp(layerEntry, compFolder, spriteItems, spriteMeta, duration, fps) {
        var pre = layerEntry.precomp;
        if (!pre) {
            return null;
        }
        var compName = safeName(layerEntry.name || "Layer") + "_precomp_" + layerEntry.id;
        var precomp = app.project.items.addComp(
            compName,
            Math.max(1, Math.ceil(pre.width || 1)),
            Math.max(1, Math.ceil(pre.height || 1)),
            1.0,
            duration,
            fps
        );
        if (compFolder) {
            precomp.parentFolder = compFolder;
        }
        var spriteLayers = {};
        var sprites = pre.sprites || [];
        for (var i = 0; i < sprites.length; i++) {
            var spriteEntry = sprites[i];
            var spriteId = spriteEntry.sprite_id;
            var footage = spriteItems[spriteId];
            if (!footage) {
                continue;
            }
            var meta = spriteMeta[spriteId] || {};
            var meshLayers = null;
            if (spriteEntry.mesh && spriteEntry.mesh.triangles && spriteEntry.mesh.triangles.length) {
                meshLayers = buildMeshSpriteLayers(precomp, spriteId, footage, meta, spriteEntry, duration);
            }
            if (meshLayers && meshLayers.length) {
                if (!spriteLayers[spriteId]) {
                    spriteLayers[spriteId] = [];
                }
                for (var ml = 0; ml < meshLayers.length; ml++) {
                    spriteLayers[spriteId].push(meshLayers[ml]);
                }
                continue;
            }
            var layer = precomp.layers.add(footage);
            layer.name = meta.name || spriteId;
            layer.property("Anchor Point").setValue([0, 0]);
            layer.property("Position").setValue([
                toNumber(spriteEntry.offset[0], 0),
                toNumber(spriteEntry.offset[1], 0)
            ]);
            var scaleValue = toNumber(meta.scale, 1.0) * 100.0;
            layer.property("Scale").setValue([scaleValue, scaleValue]);
            layer.property("Rotation").setValue(0);
            layer.property("Opacity").setValue(100);
            layer.startTime = 0;
            layer.inPoint = 0;
            layer.outPoint = duration;
            spriteLayers[spriteId] = [layer];
        }
        var spriteKeys = layerEntry.sprite_keys || [];
        if (spriteKeys.length) {
            for (var id in spriteLayers) {
                if (!spriteLayers.hasOwnProperty(id)) {
                    continue;
                }
                var layerList = spriteLayers[id];
                if (!layerList || !layerList.length) {
                    continue;
                }
                for (var li = 0; li < layerList.length; li++) {
                    applySpriteOpacityKeys(layerList[li], spriteKeys, id);
                }
            }
        }
        return precomp;
    }

    function buildLayer(
        comp,
        entry,
        precomp,
        bakedItem,
        worldParent,
        duration,
        applyTransforms
    ) {
        var layer = null;
        if (entry.export_mode === "null") {
            layer = comp.layers.addNull();
            if (entry.layer_anchor && entry.layer_anchor.length >= 2) {
                layer.property("Anchor Point").setValue([
                    toNumber(entry.layer_anchor[0], 0),
                    toNumber(entry.layer_anchor[1], 0)
                ]);
            } else {
                layer.property("Anchor Point").setValue([0, 0]);
            }
        } else if (entry.export_mode === "baked_image") {
            if (!bakedItem) {
                return null;
            }
            layer = comp.layers.add(bakedItem);
            layer.property("Anchor Point").setValue([0, 0]);
        } else {
            if (!precomp) {
                return null;
            }
            layer = comp.layers.add(precomp);
            layer.property("Anchor Point").setValue([
                toNumber(entry.precomp.anchor[0], 0),
                toNumber(entry.precomp.anchor[1], 0)
            ]);
        }
        layer.name = (entry.name || "layer") + " [" + entry.id + "]";
        layer.startTime = 0;
        layer.inPoint = 0;
        layer.outPoint = duration;
        layer.blendingMode = resolveBlendMode(entry.blend_mode);
        if (!entry.visible) {
            layer.enabled = false;
        }
        if (entry.mask_role === "mask_source") {
            layer.guideLayer = true;
        }
        if (applyTransforms) {
            applyLayerTransforms(layer, entry);
        }
        if (worldParent) {
            layer.parent = worldParent;
        }
        return layer;
    }

    function applyLayerTransforms(layer, entry) {
        if (!layer || !entry) {
            return;
        }
        if (entry.export_mode === "rig" || entry.export_mode === "null") {
            var t = entry.transform || {};
            if (t.position) {
                applyKeyframes(layer.property("Position"), t.position, true);
            } else {
                layer.property("Position").setValue([0, 0]);
            }
            if (t.scale) {
                applyKeyframes(layer.property("Scale"), t.scale, true);
            } else {
                layer.property("Scale").setValue([100, 100]);
            }
            if (t.rotation) {
                applyKeyframes(layer.property("Rotation"), t.rotation, true);
            } else {
                layer.property("Rotation").setValue(0);
            }
            if (t.opacity) {
                applyKeyframes(layer.property("Opacity"), t.opacity, true);
            } else {
                layer.property("Opacity").setValue(100);
            }
        } else if (entry.export_mode === "baked_transform") {
            var samples = (entry.transform || {}).samples || [];
            if (samples.length) {
                applySampleKeys(layer.property("Position"), samples, function (s) {
                    return [
                        toNumber(s.position[0], 0),
                        toNumber(s.position[1], 0)
                    ];
                });
                applySampleKeys(layer.property("Scale"), samples, function (s) {
                    return [
                        toNumber(s.scale[0], 100),
                        toNumber(s.scale[1], 100)
                    ];
                });
                applySampleKeys(layer.property("Rotation"), samples, function (s) {
                    return toNumber(s.rotation, 0);
                });
                applySampleKeys(layer.property("Opacity"), samples, function (s) {
                    return toNumber(s.opacity, 100);
                });
            } else {
                layer.property("Position").setValue([0, 0]);
                layer.property("Scale").setValue([100, 100]);
                layer.property("Rotation").setValue(0);
                layer.property("Opacity").setValue(100);
            }
        } else if (entry.export_mode === "baked_image") {
            layer.property("Position").setValue([0, 0]);
            layer.property("Scale").setValue([bakedScalePercent, bakedScalePercent]);
            layer.property("Rotation").setValue(0);
            layer.property("Opacity").setValue(100);
        }
    }

    function applyParentOriginOffset(entry, layerMap, originMap) {
        if (!entry || !layerMap || !originMap) {
            return;
        }
        var parentId = entry.parent_id;
        if (parentId === null || parentId === undefined || parentId < 0) {
            return;
        }
        var parentEntry = layerMap[parentId];
        if (!parentEntry) {
            return;
        }
        if (parentEntry.export_mode !== "rig" && parentEntry.export_mode !== "null") {
            return;
        }
        var offset = originMap[parentId] || [0, 0];
        if (Math.abs(offset[0]) < 0.0001 && Math.abs(offset[1]) < 0.0001) {
            return;
        }
        var transform = entry.transform || {};
        var posKeys = transform.position || [];
        for (var i = 0; i < posKeys.length; i++) {
            var key = posKeys[i];
            if (!key || !key.value || key.value.length < 2) {
                continue;
            }
            key.value[0] -= offset[0];
            key.value[1] -= offset[1];
        }
    }

    function applyTrackMattes(layerEntries, layerMap) {
        for (var i = 0; i < layerEntries.length; i++) {
            var entry = layerEntries[i];
            if (entry.mask_source_id === null || entry.mask_source_id === undefined) {
                continue;
            }
            if (entry.export_mode === "baked_image") {
                continue;
            }
            var consumer = layerMap[entry.id];
            var matte = layerMap[entry.mask_source_id];
            if (!consumer || !matte) {
                continue;
            }
            matte.moveBefore(consumer);
            matte.guideLayer = true;
            consumer.trackMatteType = TrackMatteType.ALPHA_INVERTED;
        }
    }

    function applyAttachmentOffsets(layerEntries, offset) {
        if (!layerEntries || !offset) {
            return;
        }
        for (var i = 0; i < layerEntries.length; i++) {
            var entry = layerEntries[i];
            if (entry.export_mode !== "baked_transform") {
                continue;
            }
            var samples = (entry.transform || {}).samples || [];
            for (var s = 0; s < samples.length; s++) {
                samples[s].position[0] += offset[0];
                samples[s].position[1] += offset[1];
            }
        }
    }

    var scriptFile = new File($.fileName);
    var exportRoot = scriptFile.parent;
    var manifestFile = new File(joinPath(exportRoot, "ae_manifest.json"));
    var manifest = readJSON(manifestFile);
    if (!manifest || !manifest.animation) {
        alert("AE Rig Import: Missing or invalid ae_manifest.json.");
        return;
    }

    app.beginUndoGroup("Import AE Rig");
    try {
        if (!app.project) {
            app.newProject();
        }

        var anim = manifest.animation;
        var fps = toNumber(anim.fps, 30);
        var duration = toNumber(anim.duration, 1.0);
        var viewportW = Math.round(toNumber(anim.viewport_width, 1920));
        var viewportH = Math.round(toNumber(anim.viewport_height, 1080));
        var worldOffset = [toNumber(anim.world_offset_x, 0), toNumber(anim.world_offset_y, 0)];
        var bakedScale = toNumber(anim.baked_scale, 1.0);
        if (bakedScale <= 0) {
            bakedScale = 1.0;
        }
        var bakedScalePercent = 100.0 / bakedScale;

        var rootFolder = app.project.items.addFolder("AE_Rig_" + safeName(anim.name || "animation"));
        var spritesFolder = app.project.items.addFolder("Sprites");
        spritesFolder.parentFolder = rootFolder;
        var compsFolder = app.project.items.addFolder("Comps");
        compsFolder.parentFolder = rootFolder;
        var bakedFolder = app.project.items.addFolder("Baked");
        bakedFolder.parentFolder = rootFolder;
        var audioFolder = app.project.items.addFolder("Audio");
        audioFolder.parentFolder = rootFolder;

        var spriteMeta = {};
        var sprites = manifest.sprites || [];
        for (var i = 0; i < sprites.length; i++) {
            spriteMeta[sprites[i].id] = sprites[i];
        }

        var spriteItems = {};
        for (var j = 0; j < sprites.length; j++) {
            var sprite = sprites[j];
            var spritePath = joinPath(exportRoot, sprite.path);
            var item = importFootage(spritePath, spritesFolder, fps, false);
            if (item) {
                spriteItems[sprite.id] = item;
            }
        }

        var sequenceItems = {};
        var layers = manifest.layers || [];
        for (var k = 0; k < layers.length; k++) {
            var entry = layers[k];
            if (entry.export_mode !== "baked_image" || !entry.baked_sequence) {
                continue;
            }
            var seq = entry.baked_sequence;
            var seqKey = seq.folder;
            if (sequenceItems[seqKey]) {
                continue;
            }
            var firstFile = formatFrameName(seq.pattern, 1);
            var seqPath = joinPath(exportRoot, seq.folder + "/" + firstFile);
            var seqItem = importFootage(seqPath, bakedFolder, fps, true);
            if (seqItem) {
                sequenceItems[seqKey] = seqItem;
            }
        }

        var backgroundItem = null;
        if (manifest.background && manifest.background.path) {
            var bgPath = joinPath(exportRoot, manifest.background.path);
            backgroundItem = importFootage(bgPath, bakedFolder, fps, false);
        }

        var audioItem = null;
        if (manifest.audio && manifest.audio.path) {
            var audioPath = joinPath(exportRoot, manifest.audio.path);
            audioItem = importFootage(audioPath, audioFolder, fps, false);
        }

        var mainComp = app.project.items.addComp(
            safeName(anim.name || "animation") + "_AE",
            viewportW,
            viewportH,
            1.0,
            duration,
            fps
        );
        mainComp.parentFolder = compsFolder;

        if (audioItem) {
            var audioLayer = mainComp.layers.add(audioItem);
            audioLayer.name = "Audio";
            audioLayer.startTime = 0;
            audioLayer.inPoint = 0;
            audioLayer.outPoint = duration;
        }

        var cameraNull = createNull(mainComp, "Camera_CTRL", [toNumber(anim.camera_x, 0), toNumber(anim.camera_y, 0)], [100, 100]);
        var zoomScale = toNumber(anim.render_scale, 1.0) * 100.0;
        var zoomNull = createNull(mainComp, "Zoom_CTRL", [0, 0], [zoomScale, zoomScale]);
        zoomNull.parent = cameraNull;
        var centerPos = anim.centered ? [viewportW / 2.0, viewportH / 2.0] : [0, 0];
        var centerNull = createNull(mainComp, "Center_CTRL", centerPos, [100, 100]);
        centerNull.parent = zoomNull;
        var worldNull = createNull(
            mainComp,
            "WorldOffset_CTRL",
            [0, 0],
            [100, 100]
        );
        worldNull.parent = centerNull;

        var precompMap = {};
        var aeLayerMap = {};

        var entries = [];
        for (var m = 0; m < layers.length; m++) {
            entries.push({ type: "layer", data: layers[m], stack: toNumber(layers[m].stack_index, m) });
        }
        var attachments = manifest.attachments || [];
        for (var n = 0; n < attachments.length; n++) {
            entries.push({
                type: "attachment",
                data: attachments[n],
                stack: toNumber(attachments[n].stack_index, n),
                attachment_index: n
            });
        }
        if (backgroundItem) {
            entries.push({ type: "background", data: { stack_index: 999999 }, stack: 999999 });
        }

        entries.sort(function (a, b) {
            return b.stack - a.stack;
        });

        for (var e = 0; e < entries.length; e++) {
            var entryWrap = entries[e];
            if (entryWrap.type === "background") {
                var bgLayer = mainComp.layers.add(backgroundItem);
                bgLayer.name = "Background";
                bgLayer.property("Anchor Point").setValue([0, 0]);
                bgLayer.property("Position").setValue([0, 0]);
                bgLayer.property("Scale").setValue([bakedScalePercent, bakedScalePercent]);
                bgLayer.property("Rotation").setValue(0);
                bgLayer.property("Opacity").setValue(100);
                bgLayer.parent = centerNull;
                bgLayer.startTime = 0;
                bgLayer.inPoint = 0;
                bgLayer.outPoint = duration;
                continue;
            }
            if (entryWrap.type === "attachment") {
                var attachment = entryWrap.data;
                var attName = safeName(attachment.name || "attachment");
                var attComp = app.project.items.addComp(
                    attName + "_comp",
                    Math.max(1, Math.ceil(attachment.width || viewportW)),
                    Math.max(1, Math.ceil(attachment.height || viewportH)),
                    1.0,
                    duration,
                    fps
                );
                attComp.parentFolder = compsFolder;

                var attLayers = attachment.layers || [];
                var attOffset = attachment.offset || [0, 0];
                applyAttachmentOffsets(attLayers, attOffset);

                var attEntries = [];
                for (var al = 0; al < attLayers.length; al++) {
                    attEntries.push(attLayers[al]);
                }
                attEntries.sort(function (a, b) {
                    return toNumber(b.stack_index, 0) - toNumber(a.stack_index, 0);
                });

                var attLayerMap = {};
                for (var ai = 0; ai < attEntries.length; ai++) {
                    var attEntry = attEntries[ai];
                    var attPrecomp = null;
                    if (attEntry.precomp) {
                        attPrecomp = buildLayerPrecomp(attEntry, compsFolder, spriteItems, spriteMeta, duration, fps);
                    }
                    var attLayer = buildLayer(attComp, attEntry, attPrecomp, null, null, duration, true);
                    if (attLayer) {
                        attLayerMap[attEntry.id] = attLayer;
                    }
                }
                applyTrackMattes(attEntries, attLayerMap);

                var attLayerMain = mainComp.layers.add(attComp);
                attLayerMain.name = "Attachment_" + attName;
                attLayerMain.property("Anchor Point").setValue([toNumber(attOffset[0], 0), toNumber(attOffset[1], 0)]);
                attLayerMain.property("Position").setValue([0, 0]);
                attLayerMain.property("Scale").setValue([100, 100]);
                attLayerMain.property("Rotation").setValue(0);
                attLayerMain.property("Opacity").setValue(100);
                attLayerMain.startTime = 0;
                attLayerMain.inPoint = 0;
                attLayerMain.outPoint = duration;
                aeLayerMap["attachment_index_" + entryWrap.attachment_index] = attLayerMain;
                continue;
            }

            var layerEntry = entryWrap.data;
            var precompKey = "layer_" + layerEntry.id;
            var precomp = null;
            if (layerEntry.precomp) {
                precomp = precompMap[precompKey];
                if (!precomp) {
                    precomp = buildLayerPrecomp(layerEntry, compsFolder, spriteItems, spriteMeta, duration, fps);
                    precompMap[precompKey] = precomp;
                }
            }
            var bakedItem = null;
            if (layerEntry.export_mode === "baked_image" && layerEntry.baked_sequence) {
                bakedItem = sequenceItems[layerEntry.baked_sequence.folder];
            }
            var aeLayer = buildLayer(mainComp, layerEntry, precomp, bakedItem, null, duration, false);
            if (aeLayer) {
                aeLayerMap[layerEntry.id] = aeLayer;
            }
        }

        var layerDataMap = {};
        for (var l = 0; l < layers.length; l++) {
            layerDataMap[layers[l].id] = layers[l];
        }
        var originMap = {};
        for (var l = 0; l < layers.length; l++) {
            var origin = [0, 0];
            if (layers[l].precomp && layers[l].precomp.origin) {
                origin = layers[l].precomp.origin;
            }
            originMap[layers[l].id] = origin;
        }
        for (var l = 0; l < layers.length; l++) {
            var layerData = layers[l];
            var aeLayerRef = aeLayerMap[layerData.id];
            if (!aeLayerRef) {
                continue;
            }
            if (layerData.export_mode === "baked_image" || layerData.export_mode === "baked_transform") {
                aeLayerRef.parent = worldNull;
                continue;
            }
            if (layerData.parent_id !== null && layerData.parent_id >= 0) {
                var parentLayer = aeLayerMap[layerData.parent_id];
                var parentData = layerDataMap[layerData.parent_id];
                if (parentLayer && parentData && (parentData.export_mode === "rig" || parentData.export_mode === "null")) {
                    aeLayerRef.parent = parentLayer;
                    continue;
                }
            }
            aeLayerRef.parent = worldNull;
        }

        for (var a = 0; a < attachments.length; a++) {
            var att = attachments[a];
            var attLayer = aeLayerMap["attachment_index_" + a];
            if (!attLayer) {
                continue;
            }
            var targetLayer = aeLayerMap[att.target_layer_id];
            if (targetLayer) {
                attLayer.parent = targetLayer;
            } else {
                attLayer.parent = worldNull;
            }
        }

        for (var t = 0; t < layers.length; t++) {
            var entryData = layers[t];
            var entryLayer = aeLayerMap[entryData.id];
            if (entryLayer) {
                applyParentOriginOffset(entryData, layerDataMap, originMap);
                applyLayerTransforms(entryLayer, entryData);
            }
        }

        applyTrackMattes(layers, aeLayerMap);
        applyWorldOffsetToRoots(mainComp, worldNull, worldOffset);

        cameraNull.moveToBeginning();
        zoomNull.moveToBeginning();
        centerNull.moveToBeginning();
        worldNull.moveToBeginning();

        alert("AE Rig Import: Complete. Main comp: " + mainComp.name);
    } catch (err) {
        alert("AE Rig Import: Failed. " + err.toString());
    } finally {
        app.endUndoGroup();
    }
})();

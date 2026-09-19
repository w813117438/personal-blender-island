/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup edanimation
 */

#include <algorithm>
#include <cmath>

#include "DNA_action_types.h"
#include "DNA_anim_types.h"
#include "DNA_object_types.h"

#include "RNA_access.hh"
#include "RNA_prototypes.hh"

#include "BLI_math_rotation_c.hh"
#include "BLI_math_vector_c.hh"
#include "BLI_set.hh"

#include "BKE_context.hh"
#include "BKE_fcurve.hh"
#include "BKE_lib_id.hh"

#include "ANIM_action.hh"
#include "ANIM_action_iterators.hh"
#include "ANIM_fcurve.hh"
#include "ANIM_rna.hh"

#include "DEG_depsgraph.hh"

#include "WM_api.hh"

#include "ED_anim_api.hh"
#include "ED_anim_transformable.hh"

namespace blender {

void SortedFCurveBuffer::insert_fcurve(FCurve &fcurve)
{
  int insert_index = 0;
  for (FCurve *existing_fcurve : fcurves_) {
    BLI_assert_msg(fcurve.array_index != existing_fcurve->array_index,
                   "An FCurve with that index was already inserted");
    if (existing_fcurve->array_index > fcurve.array_index) {
      break;
    }
    insert_index++;
  }
  fcurves_.insert(insert_index, &fcurve);
}

void SortedFCurveBuffer::clear()
{
  fcurves_.clear();
}

Span<FCurve *> SortedFCurveBuffer::fcurves() const
{
  return fcurves_;
}

FCurve *SortedFCurveBuffer::get_fcurve_by_array_index(const int array_index) const
{
  for (FCurve *fcurve : fcurves_) {
    if (fcurve->array_index == array_index) {
      return fcurve;
    }
  }
  return nullptr;
}

/* Returns the ranges in which a rotation mode is active. Each entry denotes the starting point of
 * the range and it ends with the next entry. If the FCurve has any keys, there will be at least
 * one entry with the starting mode. */
static Vector<std::pair<float, eRotationModes>> get_rotation_mode_ranges(const FCurve &fcurve)
{
  BLI_assert(fcurve.rna_path().endswith("rotation_mode"));
  if (!fcurve.bezt) {
    return {};
  }
  Vector<std::pair<float, eRotationModes>> changes;
  for (const int i : IndexRange(fcurve.totvert)) {
    const BezTriple &key = fcurve.bezt[i];
    const eRotationModes key_rotation_mode = eRotationModes(key.vec[1][1]);
    if (changes.is_empty()) {
      changes.append({0, key_rotation_mode});
      continue;
    }
    if (changes.last().second == key_rotation_mode) {
      continue;
    }
    changes.append({key.vec[1][0], key_rotation_mode});
  }
  return changes;
}

/**
 * Iterates all frames with keys on the given FCurves in the given range. Every frame is visited in
 * ascending order only once.
 */
class KeyframeIterator {
  Vector<const FCurve *> fcurves_;
  Array<int> key_indices_;
  Bounds<float> range_;

  float get_next_frame() const
  {
    float next_frame = FLT_MAX;
    for (const int i : fcurves_.index_range()) {
      const FCurve *fcurve = fcurves_[i];
      if (key_indices_[i] > fcurve->totvert - 1) {
        continue;
      }
      const float key_frame = fcurve->bezt[key_indices_[i]].vec[1][0];
      if (key_frame >= range_.max) {
        continue;
      }
      if (key_frame < next_frame) {
        next_frame = key_frame;
      }
    }
    return next_frame;
  }

 public:
  /**
   * \param fcurves: Allowed to have nullptr entries.
   * \param range: Interpreted as inclusive/exclusive.
   */
  KeyframeIterator(Span<const FCurve *> fcurves, const Bounds<float> range) : range_(range)
  {
    for (const int i : fcurves.index_range()) {
      if (!fcurves[i] || !fcurves[i]->bezt) {
        continue;
      }
      fcurves_.append(fcurves[i]);
    }

    key_indices_.reinitialize(fcurves_.size());
    bool frame_has_key;
    for (const int i : fcurves_.index_range()) {
      const FCurve *fcurve = fcurves_[i];
      const int index = BKE_fcurve_bezt_binarysearch_index(
          fcurve->bezt, range.min, fcurve->totvert, &frame_has_key);
      key_indices_[i] = index;
    }
  }

  bool can_advance() const
  {
    for (const int i : fcurves_.index_range()) {
      const FCurve *fcurve = fcurves_[i];
      if (key_indices_[i] > fcurve->totvert - 1) {
        /* No more keys for that FCurve. */
        continue;
      }
      const float key_frame = fcurve->bezt[key_indices_[i]].vec[1][0];
      if (key_frame < range_.max) {
        return true;
      }
    }
    return false;
  }

  void advance()
  {
    float next_frame = get_next_frame();

    if (next_frame == FLT_MAX) {
      /* No more keys to step in the range. */
      return;
    }

    for (const int i : fcurves_.index_range()) {
      const FCurve *fcurve = fcurves_[i];
      if (key_indices_[i] > fcurve->totvert - 1) {
        continue;
      }
      const float key_frame = fcurve->bezt[key_indices_[i]].vec[1][0];
      if (key_frame <= next_frame) {
        /* Advance all FCurves that have a key at that frame. */
        key_indices_[i]++;
      }
    }
  }

  /**
   * Returns the frame of the current iteration step. If the iterator has completed it will always
   * return 0.
   */
  float get_frame() const
  {
    float next_frame = get_next_frame();

    if (next_frame == FLT_MAX) {
      /* No more keys to step in the range. */
      return 0;
    }
    return next_frame;
  }

  /**
   * Returns the keyframe settings of the current step. The first FCurve with a key at the current
   * frame is used for this.
   */
  animrig::KeyframeSettings get_keyframe_settings() const
  {
    /* Always return some reasonable defaults. */
    animrig::KeyframeSettings settings = {BEZT_KEYTYPE_KEYFRAME, HD_AUTO, BEZT_IPO_BEZ};
    BezTriple *key = nullptr;
    for (const int i : fcurves_.index_range()) {
      const FCurve *fcurve = fcurves_[i];
      if (key_indices_[i] > fcurve->totvert - 1) {
        continue;
      }
      if (key && fcurve->bezt[key_indices_[i]].vec[1][0] >= key->vec[1][0]) {
        continue;
      }
      key = &fcurve->bezt[key_indices_[i]];
    }
    if (key) {
      settings.handle = eBezTriple_Handle(key->h1);
      settings.interpolation = eBezTriple_Interpolation(key->ipo);
      settings.keyframe_type = BEZKEYTYPE(key);
    }
    return settings;
  }
};

/**
 * For all keyframes in the `evaluation_buffer` in the given range, insert values into
 * `insertion_buffer` that represent the same rotation but in the given rotation mode.
 *
 * \param evaluation_buffer: It is assumed that those FCurves match the rotation mode `from_mode`.
 * They will be read for keyframe values. It is allowed to have nullptr FCurves in here.
 * \param insertion_buffer: The FCurves relating to `to_mode`. Keyframes for the converted rotation
 * mode will be inserted here. None of the FCurves shall be a nullptr.
 * \param range: Start and end frames to limit the range in which to convert and insert rotation
 * keys. Interpreted inclusive at the start and exclusive at the end and in FCurve time.
 * \param ensure_range_start_key: If set to true a key will be set at `range.min` regardless of a
 * key existing on that frame in the evaluation buffer.
 */
static void convert_rotation_mode_range(const Span<const FCurve *> evaluation_buffer,
                                        const Span<FCurve *> insertion_buffer,
                                        const eRotationModes from_mode,
                                        const eRotationModes to_mode,
                                        const Bounds<float> range,
                                        const ed::AnimTransformable &transformable,
                                        const bool ensure_range_start_key)
{
  /* Sample for error measurement, but only store keys needed to reproduce the motion.
   * Original key times are mandatory boundaries, including fractional/negative frames. */
  Vector<float> frames;
  Vector<animrig::KeyframeSettings> key_settings;
  KeyframeIterator iterator(evaluation_buffer, range);
  if (ensure_range_start_key && (!iterator.can_advance() || iterator.get_frame() > range.min)) {
    frames.append(range.min);
    key_settings.append(iterator.get_keyframe_settings());
  }
  while (iterator.can_advance()) {
    frames.append(iterator.get_frame());
    key_settings.append(iterator.get_keyframe_settings());
    iterator.advance();
  }
  if (frames.is_empty()) {
    return;
  }

  auto evaluate_rotation = [&](const float frame) {
    ed::Rotation rotation = transformable.get_rotation_for_mode(from_mode);
    for (const FCurve *curve : evaluation_buffer) {
      if (curve) {
        rotation.values[curve->array_index] = evaluate_fcurve(curve, frame);
      }
    }
    return rotation;
  };
  auto compatible = [&](const ed::Rotation &source, const ed::Rotation *previous) {
    ed::Rotation converted = source.converted_to_mode(to_mode,
        to_mode > ROT_MODE_QUAT ? previous : nullptr);
    if (previous && to_mode == ROT_MODE_QUAT &&
        dot_v4v4(converted.values.data(), previous->values.data()) < 0.0f) {
      negate_v4(converted.values.data());
    }
    if (previous && to_mode == ROT_MODE_AXISANGLE) {
      if (fabsf(sinf(converted.values[0] * 0.5f)) < 1e-6f) {
        copy_v3_v3(&converted.values[1], &previous->values[1]);
      }
      else if (dot_v3v3(&converted.values[1], &previous->values[1]) < 0.0f) {
        negate_v3(&converted.values[1]);
        converted.values[0] = -converted.values[0];
      }
      converted.values[0] += float(2.0 * M_PI) *
          roundf((previous->values[0] - converted.values[0]) / float(2.0 * M_PI));
    }
    return converted;
  };
  auto error = [](const ed::Rotation &a, const ed::Rotation &b) {
    ed::Rotation qa = a.converted_to_mode(ROT_MODE_QUAT);
    ed::Rotation qb = b.converted_to_mode(ROT_MODE_QUAT);
    normalize_qt(qa.values.data());
    normalize_qt(qb.values.data());
    const double sign = dot_v4v4(qa.values.data(), qb.values.data()) < 0.0f ? -1.0 : 1.0;
    double difference = 0.0, sum = 0.0;
    for (int i = 0; i < 4; i++) {
      const double d = qa.values[i] - sign * qb.values[i];
      const double p = qa.values[i] + sign * qb.values[i];
      difference += d * d;
      sum += p * p;
    }
    return 4.0 * atan2(sqrt(difference), sqrt(sum));
  };
  auto insert = [&](const float frame, const ed::Rotation &rotation,
                    animrig::KeyframeSettings settings) {
    settings.interpolation = BEZT_IPO_LIN;
    settings.handle = HD_AUTO_ANIM;
    for (const int i : insertion_buffer.index_range()) {
      insert_vert_fcurve(insertion_buffer[i], {frame, rotation.values[i]}, settings, INSERTKEY_FAST);
    }
  };

  ed::Rotation previous = compatible(evaluate_rotation(frames[0]), nullptr);
  if (to_mode == ROT_MODE_AXISANGLE && frames.size() > 1 &&
      fabsf(sinf(previous.values[0] * 0.5f)) < 1e-6f) {
    const ed::Rotation next = compatible(
        evaluate_rotation(std::min(float(floor(double(frames[0])) + 1.0), frames[1])), nullptr);
    copy_v3_v3(&previous.values[1], &next.values[1]);
  }
  insert(frames[0], previous, key_settings[0]);
  constexpr double tolerance = 0.05 * M_PI / 180.0;
  for (int interval = 0; interval + 1 < frames.size(); interval++) {
    const float first = frames[interval], last = frames[interval + 1];
    /* Original key times remain mandatory. Every additional candidate is an integer
     * timeline frame, including when the original keys are negative or fractional. */
    Vector<float> times;
    Vector<ed::Rotation> samples;
    times.append(first);
    samples.append(previous);
    for (double frame = floor(double(first)) + 1.0; frame < double(last); frame += 1.0) {
      const float time = float(frame);
      if (time <= times.last() || time >= last) {
        continue;
      }
      times.append(time);
      samples.append(compatible(evaluate_rotation(time), &samples.last()));
    }
    times.append(last);
    samples.append(compatible(evaluate_rotation(last), &samples.last()));

    /* Preserve step transitions exactly when every varying source channel is held. */
    bool held = true;
    for (const FCurve *curve : evaluation_buffer) {
      if (!curve || !curve->bezt) {
        continue;
      }
      bool found;
      int index = BKE_fcurve_bezt_binarysearch_index(curve->bezt, first, curve->totvert, &found);
      if (!found) {
        index--;
      }
      if (index >= 0 && index + 1 < curve->totvert &&
          curve->bezt[index].ipo != BEZT_IPO_CONST) {
        held = false;
      }
    }
    if (held) {
      for (FCurve *curve : insertion_buffer) {
        curve->bezt[curve->totvert - 1].ipo = BEZT_IPO_CONST;
      }
      insert(last, samples.last(), key_settings[interval + 1]);
      previous = samples.last();
      continue;
    }

    const int last_index = times.size() - 1;
    Vector<bool> keep(times.size(), false);
    keep[0] = keep[last_index] = true;
    Vector<std::pair<int, int>> pending;
    pending.append({0, last_index});
    while (!pending.is_empty()) {
      const auto [a, b] = pending.pop_last();
      double worst = tolerance;
      int split = -1;
      for (int i = a + 1; i < b; i++) {
        const float factor = (times[i] - times[a]) / (times[b] - times[a]);
        ed::Rotation candidate = samples[a];
        for (const int axis : candidate.values.index_range()) {
          candidate.values[axis] = interpf(samples[b].values[axis], samples[a].values[axis], factor);
        }
        const double distance = error(samples[i], candidate);
        if (distance > worst) {
          worst = distance;
          split = i;
        }
      }
      if (split >= 0) {
        keep[split] = true;
        pending.append({a, split});
        pending.append({split, b});
      }
    }
    for (int i = 1; i <= last_index; i++) {
      if (keep[i]) {
        auto settings = key_settings[interval + 1];
        if (i != last_index) {
          settings.keyframe_type = BEZT_KEYTYPE_BREAKDOWN;
        }
        insert(times[i], samples[i], settings);
      }
    }
    previous = samples.last();
  }
}

static void remove_rotation_fcurves(const ed::AnimTransformable &transformable,
                                    RNAFCurveMap &fcu_map,
                                    animrig::Channelbag &channelbag,
                                    const eRotationModes rotation_mode)
{
  std::string rna_path = transformable.rna_path_to_rotation(rotation_mode);
  SortedFCurveBuffer *rotation_fcurves = fcu_map.lookup_ptr(rna_path);
  if (!rotation_fcurves) {
    return;
  }
  /* Remove the FCurves that target the now no longer used rotation modes. */
  for (FCurve *fcurve : rotation_fcurves->fcurves()) {
    channelbag.fcurve_remove(*fcurve);
  }
  rotation_fcurves->clear();
  fcu_map.remove(rna_path);
}

static bool convert_rotation_mode_channelbag(animrig::Channelbag &channelbag,
                                             RNAFCurveMap &fcu_map,
                                             const eRotationModes to_mode,
                                             const Span<std::pair<float, eRotationModes>> ranges,
                                             const ed::AnimTransformable &transformable)
{
  const int insertion_buffer_count = to_mode > ROT_MODE_QUAT ? 3 : 4;
  Array<FCurve *> insertion_buffer(insertion_buffer_count);

  for (const int i : insertion_buffer.index_range()) {
    /* Is needed to get correct FCurve colors. */
    PropertySubType prop_subtype = PROP_EULER;
    if (to_mode == ROT_MODE_QUAT) {
      prop_subtype = PROP_QUATERNION;
    }
    else if (to_mode == ROT_MODE_AXISANGLE) {
      prop_subtype = PROP_AXISANGLE;
    }
    FCurve *fcurve = animrig::create_fcurve_for_channel(
        {transformable.rna_path_to_rotation(to_mode),
         i,
         PROP_FLOAT,
         prop_subtype,
         transformable.fcurve_group_name()});
    insertion_buffer[i] = fcurve;
  }

  bool modified_keys = false;
  for (const int i : ranges.index_range()) {
    const std::pair<float, eRotationModes> &rotation_mode_range = ranges[i];
    const eRotationModes from_mode = rotation_mode_range.second;

    const std::string from_mode_rna_path = transformable.rna_path_to_rotation(from_mode);
    const SortedFCurveBuffer *rotation_fcurves = fcu_map.lookup_ptr(from_mode_rna_path);
    if (!rotation_fcurves) {
      continue;
    }

    const int evaluation_buffer_count = from_mode > ROT_MODE_QUAT ? 3 : 4;
    Array<FCurve *> evaluation_buffer(evaluation_buffer_count);
    for (const int buffer_index : evaluation_buffer.index_range()) {
      evaluation_buffer[buffer_index] = rotation_fcurves->get_fcurve_by_array_index(buffer_index);
    }

    Bounds<float> range(rotation_mode_range.first, FLT_MAX);
    if (i + 1 < ranges.size()) {
      range.max = ranges[i + 1].first;
    }

    convert_rotation_mode_range(
        evaluation_buffer, insertion_buffer, from_mode, to_mode, range, transformable, i > 0);

    modified_keys = true;
  }

  if (!modified_keys) {
    /* There were no rotation FCurves to read from. In that case don't insert the
     * `insertion_buffer` FCurves into the channelbag. */
    for (FCurve *fcurve : insertion_buffer) {
      BKE_fcurve_free(fcurve);
    }
    return false;
  }

  /* Remove all old rotation FCurves. */
  remove_rotation_fcurves(transformable, fcu_map, channelbag, ROT_MODE_QUAT);
  remove_rotation_fcurves(transformable, fcu_map, channelbag, ROT_MODE_EUL);
  remove_rotation_fcurves(transformable, fcu_map, channelbag, ROT_MODE_AXISANGLE);

  for (FCurve *fcurve : insertion_buffer) {
    channelbag.fcurve_append(*fcurve);
    bActionGroup &grp = channelbag.channel_group_ensure(transformable.fcurve_group_name());
    channelbag.fcurve_assign_to_channel_group(*fcurve, grp);
    BKE_fcurve_handles_recalc(*fcurve);
  }
  return true;
}

bool convert_rotation_keys(const ed::AnimTransformable &transformable,
                           ChannelbagFCurveMap &channelbag_fcurve_map,
                           const eRotationModes to_mode)
{
  bool modified_keys = false;

  for (const auto &item : channelbag_fcurve_map.items()) {
    animrig::Channelbag *channelbag = item.key;
    RNAFCurveMap &fcu_map = item.value;
    const std::string rotation_mode_path = transformable.rna_path_to_rotation_mode();
    Vector<std::pair<float, eRotationModes>> rotation_mode_ranges;
    FCurve *rotation_mode_fcurve = nullptr;
    if (const SortedFCurveBuffer *rotation_mode_buffer = fcu_map.lookup_ptr(rotation_mode_path)) {
      BLI_assert(rotation_mode_buffer->fcurves().size() == 1);
      rotation_mode_fcurve = rotation_mode_buffer->fcurves()[0];
      rotation_mode_ranges = get_rotation_mode_ranges(*rotation_mode_fcurve);
    }
    else {
      /* Defaulting back to the struct value means that this can have unexpected results when
       * dealing with action layers. The rotation mode can still be animated by a higher layer but
       * that means we cannot know the correct rotation mode for the current layer. */
      if (transformable.get_rotation_mode() == to_mode) {
        continue;
      }
      rotation_mode_ranges = {{-FLT_MAX, transformable.get_rotation_mode()}};
    }

    modified_keys |= convert_rotation_mode_channelbag(
        *channelbag, fcu_map, to_mode, rotation_mode_ranges, transformable);

    if (rotation_mode_fcurve && rotation_mode_fcurve->bezt) {
      for (const int i : IndexRange(rotation_mode_fcurve->totvert)) {
        rotation_mode_fcurve->bezt[i].vec[1][1] = to_mode;
      }
      BKE_fcurve_handles_recalc(*rotation_mode_fcurve);
    }
  }

  return modified_keys;
}

static bool is_rotation_mode_path(const StringRefNull rna_path)
{
  const int start_of_propname = rna_path.rfind(".") + 1;
  return rna_path.substr(start_of_propname, rna_path.size()) == "rotation_mode";
}

ChannelbagFCurveMap build_rotation_fcurve_map(animrig::Action &action,
                                              const animrig::slot_handle_t slot_handle)
{
  ChannelbagFCurveMap rotation_map;
  for (animrig::Channelbag *channelbag : channelbags_for_action_slot(action, slot_handle)) {
    RNAFCurveMap &curves = rotation_map.lookup_or_add(channelbag, {});
    for (FCurve *fcurve : channelbag->fcurves()) {
      StringRefNull rna_path = fcurve->rna_path();
      if (!animrig::is_rotation_path(fcurve->rna_path_parsed()) &&
          !is_rotation_mode_path(rna_path))
      {
        continue;
      }
      SortedFCurveBuffer &fcurve_buffer = curves.lookup_or_add(rna_path, {});
      fcurve_buffer.insert_fcurve(*fcurve);
    }
  }
  return rotation_map;
}

void bake_rotation_fcurves(const ChannelbagFCurveMap &channelbag_fcurve_map,
                           const ed::AnimTransformable &transformable)
{
  /* Need to bake on all potential FCurves to cover for an animated rotation mode. */
  const Array<eRotationModes> rotation_modes = {ROT_MODE_EUL, ROT_MODE_QUAT, ROT_MODE_AXISANGLE};
  for (const eRotationModes rotation_mode : rotation_modes) {
    std::string rotation_rna_path = transformable.rna_path_to_rotation(rotation_mode);

    for (const RNAFCurveMap &rna_fcurve_map : channelbag_fcurve_map.values()) {
      const SortedFCurveBuffer *fcurve_buffer = rna_fcurve_map.lookup_ptr(rotation_rna_path);
      if (!fcurve_buffer) {
        continue;
      }
      for (FCurve *fcurve : fcurve_buffer->fcurves()) {
        if (!fcurve || !fcurve->bezt) {
          continue;
        }
        const int2 range = {int(fcurve->bezt[0].vec[1][0]),
                            int(fcurve->bezt[fcurve->totvert - 1].vec[1][0])};
        animrig::bake_fcurve(fcurve, range, 1, animrig::BakeCurveRemove::ALL);
      }
    }
  }
}

void convert_to_rotation_mode(bContext &C,
                              ed::AnimTransformable &transformable,
                              const eRotationModes to_mode,
                              const bool bake)
{
  Main *bmain = CTX_data_main(&C);
  if (!BKE_id_is_editable(bmain, transformable.owner_id())) {
    return;
  }
  /* A map built per action to make it quicker to find the FCurves by RNA path. */
  Map<std::pair<animrig::Action *, animrig::slot_handle_t>, ChannelbagFCurveMap> data_map;

  bool converted_actions = false;
  animrig::foreach_action_slot_use(
      *transformable.owner_id(),
      [&](animrig::Action &action, const animrig::slot_handle_t slot_handle) {
        if (!BKE_id_is_editable(bmain, &action.id)) {
          return true;
        }
        if (!data_map.contains({&action, slot_handle})) {
          ChannelbagFCurveMap fcurve_map = build_rotation_fcurve_map(action, slot_handle);
          data_map.add({&action, slot_handle}, fcurve_map);
        }
        ChannelbagFCurveMap &channelbag_fcurve_map = data_map.lookup({&action, slot_handle});
        if (bake) {
          bake_rotation_fcurves(channelbag_fcurve_map, transformable);
        }
        converted_actions |= convert_rotation_keys(transformable, channelbag_fcurve_map, to_mode);
        DEG_id_tag_update(&action.id, ID_RECALC_ANIMATION);
        return true;
      });

  if (converted_actions) {
    transformable.set_rotation_mode(to_mode);
    ID *id = transformable.owner_id();
    DEG_id_tag_update(id, ID_RECALC_GEOMETRY);
    WM_event_add_notifier(&C, NC_ANIMATION | ND_KEYFRAME | NA_ADDED, nullptr);
  }
  else {
    ed::Rotation rotation = transformable.get_rotation();
    transformable.set_rotation_mode(to_mode);
    transformable.set_rotation(rotation.converted_to_mode(to_mode));
  }
}

}  // namespace blender

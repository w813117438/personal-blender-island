/* SPDX-FileCopyrightText: 2024 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "ANIM_action.hh"
#include "ANIM_fcurve.hh"

#include "BKE_action.hh"
#include "BKE_anim_data.hh"
#include "BKE_global.hh"
#include "BKE_gtest_base.hh"
#include "BKE_idtype.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_object.hh"

#include "DNA_anim_types.h"
#include "DNA_object_types.h"
#include "DNA_space_types.h"

#include "ED_anim_api.hh"

#include "BLI_listbase.hh"

#include "testing/testing.h"

namespace blender::animrig::tests {
class ActionFilterTest : public bke::BlenderGTestBase {
 public:
  Main *bmain;
  Action *action;
  Object *cube;
  Object *suzanne;

  void SetUp() override
  {
    bmain = BKE_main_new();
    G_MAIN = bmain; /* For BKE_animdata_free(). */

    action = &BKE_id_new<bAction>(bmain, "ACÄnimåtië")->wrap();
    cube = BKE_object_add_only_object(bmain, OB_EMPTY, "Küüübus");
    suzanne = BKE_object_add_only_object(bmain, OB_EMPTY, "OBSuzanne");
  }

  void TearDown() override
  {
    BKE_main_free(bmain);
    G_MAIN = nullptr;
  }
};

TEST_F(ActionFilterTest, slots_expanded_or_not)
{
  Slot &slot_cube = action->slot_add();
  Slot &slot_suzanne = action->slot_add();
  ASSERT_TRUE(assign_action(action, cube->id));
  ASSERT_TRUE(assign_action(action, suzanne->id));
  ASSERT_EQ(assign_action_slot(&slot_cube, cube->id), ActionSlotAssignmentResult::OK);
  ASSERT_EQ(assign_action_slot(&slot_suzanne, suzanne->id), ActionSlotAssignmentResult::OK);

  Layer &layer = action->layer_add("Kübus layer");
  Strip &key_strip = layer.strip_add(*action, Strip::Type::Keyframe);
  StripKeyframeData &strip_data = key_strip.data<StripKeyframeData>(*action);

  /* Create multiple FCurves for multiple Slots. */
  const KeyframeSettings settings = get_keyframe_settings(false);
  ASSERT_EQ(
      SingleKeyingResult::SUCCESS,
      strip_data.keyframe_insert(bmain, slot_cube, {"location", 0}, {1.0f, 0.25f}, settings));
  ASSERT_EQ(
      SingleKeyingResult::SUCCESS,
      strip_data.keyframe_insert(bmain, slot_cube, {"location", 1}, {1.0f, 0.25f}, settings));
  ASSERT_EQ(
      SingleKeyingResult::SUCCESS,
      strip_data.keyframe_insert(bmain, slot_suzanne, {"location", 0}, {1.0f, 0.25f}, settings));
  ASSERT_EQ(
      SingleKeyingResult::SUCCESS,
      strip_data.keyframe_insert(bmain, slot_suzanne, {"location", 1}, {1.0f, 0.25f}, settings));

  Channelbag *cube_channelbag = strip_data.channelbag_for_slot(slot_cube);
  ASSERT_NE(nullptr, cube_channelbag);
  FCurve *fcu_cube_loc_x = cube_channelbag->fcurve_find({"location", 0});
  FCurve *fcu_cube_loc_y = cube_channelbag->fcurve_find({"location", 1});
  ASSERT_NE(nullptr, fcu_cube_loc_x);
  ASSERT_NE(nullptr, fcu_cube_loc_y);

  /* Mock an bAnimContext for the Animation editor, with the above Animation showing. */
  SpaceAction saction = {};
  saction.ads.filterflag = eDopeSheet_FilterFlag(0);

  bAnimContext ac = {nullptr};
  ac.bmain = bmain;
  ac.datatype = ANIMCONT_ACTION;
  ac.data = action;
  ac.spacetype = SPACE_ACTION;
  ac.sl = reinterpret_cast<SpaceLink *>(&saction);
  ac.obact = cube;
  ac.active_action = action;
  ac.active_action_user = &cube->id;
  ac.ads = &saction.ads;

  { /* Test with collapsed slots. */
    slot_cube.set_expanded(false);
    slot_suzanne.set_expanded(false);

    /* This should produce 2 slots and no FCurves. */
    ListBaseT<bAnimListElem> anim_data = {nullptr, nullptr};
    eAnimFilter_Flags filter = (ANIMFILTER_DATA_VISIBLE | ANIMFILTER_LIST_VISIBLE |
                                ANIMFILTER_FOREDIT | ANIMFILTER_NODUPLIS |
                                ANIMFILTER_LIST_CHANNELS);
    const int num_entries = ANIM_animdata_filter(
        &ac, &anim_data, filter, ac.data, eAnimCont_Types(ac.datatype));
    EXPECT_EQ(2, num_entries);
    EXPECT_EQ(2, anim_data.count());

    ASSERT_GE(num_entries, 1)
        << "Missing 1st ANIMTYPE_ACTION_SLOT entry, stopping to prevent crash";
    const bAnimListElem *first_ale = static_cast<bAnimListElem *>(BLI_findlink(&anim_data, 0));
    EXPECT_EQ(ANIMTYPE_ACTION_SLOT, first_ale->type);
    EXPECT_EQ(ALE_ACTION_SLOT, first_ale->datatype);
    EXPECT_EQ(&cube->id, first_ale->id) << "id should be the animated ID (" << cube->id.name
                                        << ") but is (" << first_ale->id->name << ")";
    EXPECT_EQ(cube->adt, first_ale->adt) << "adt should be the animated ID's animation data";
    EXPECT_EQ(&action->id, first_ale->fcurve_owner_id) << "fcurve_owner_id should be the Action";
    EXPECT_EQ(&action->id, first_ale->key_data) << "key_data should be the Action";
    EXPECT_EQ(&slot_cube, first_ale->data);
    EXPECT_EQ(slot_cube.slot_flags, first_ale->flag);

    ASSERT_GE(num_entries, 2)
        << "Missing 2nd ANIMTYPE_ACTION_SLOT entry, stopping to prevent crash";
    const bAnimListElem *second_ale = static_cast<bAnimListElem *>(BLI_findlink(&anim_data, 1));
    EXPECT_EQ(ANIMTYPE_ACTION_SLOT, second_ale->type);
    EXPECT_EQ(&slot_suzanne, second_ale->data);
    /* Assume the rest is set correctly, as it's the same code as tested above. */

    ANIM_animdata_freelist(&anim_data);
  }

  { /* Expanded slots contain collapsed transform summaries. */
    slot_cube.set_expanded(true);
    slot_suzanne.set_expanded(false);
    ListBaseT<bAnimListElem> anim_data = {};
    const eAnimFilter_Flags filter = ANIMFILTER_DATA_VISIBLE | ANIMFILTER_LIST_VISIBLE |
                                      ANIMFILTER_LIST_CHANNELS | ANIMFILTER_NODUPLIS;
    EXPECT_EQ(3, ANIM_animdata_filter(&ac, &anim_data, filter, ac.data, ANIMCONT_ACTION));
    bAnimListElem *summary = anim_data.first()->next;
    ASSERT_EQ(ANIMTYPE_FCURVE_PROPERTY, summary->type);
    EXPECT_EQ(2, summary->property_curve_count);
    EXPECT_EQ(fcu_cube_loc_x, summary->property_curves[0]);
    EXPECT_EQ(fcu_cube_loc_y, summary->property_curves[1]);
    EXPECT_EQ(&slot_suzanne, summary->next->data);
    ANIM_channel_setting_set(&ac, summary, ACHANNEL_SETTING_EXPAND, ACHANNEL_SETFLAG_ADD);
    ANIM_animdata_freelist(&anim_data);

    EXPECT_EQ(5, ANIM_animdata_filter(&ac, &anim_data, filter, ac.data, ANIMCONT_ACTION));
    summary = anim_data.first()->next;
    EXPECT_EQ(ANIMTYPE_FCURVE_PROPERTY, summary->type);
    EXPECT_TRUE(summary->next->property_axis);
    EXPECT_EQ(fcu_cube_loc_x, summary->next->data);
    EXPECT_EQ(fcu_cube_loc_y, summary->next->next->data);
    ANIM_channel_setting_set(&ac, summary, ACHANNEL_SETTING_SELECT, ACHANNEL_SETFLAG_CLEAR);
    EXPECT_FALSE(fcu_cube_loc_x->flag & FCURVE_SELECTED);
    EXPECT_FALSE(fcu_cube_loc_y->flag & FCURVE_SELECTED);
    ANIM_channel_setting_set(&ac, summary, ACHANNEL_SETTING_SELECT, ACHANNEL_SETFLAG_ADD);
    EXPECT_TRUE(fcu_cube_loc_x->flag & FCURVE_SELECTED);
    EXPECT_TRUE(fcu_cube_loc_y->flag & FCURVE_SELECTED);
    ANIM_channel_setting_set(&ac, summary, ACHANNEL_SETTING_EXPAND, ACHANNEL_SETFLAG_CLEAR);
    ANIM_animdata_freelist(&anim_data);

    /* Data operations still get each F-Curve exactly once, including collapsed axes. */
    EXPECT_EQ(4, ANIM_animdata_filter(&ac, &anim_data,
        ANIMFILTER_DATA_VISIBLE | ANIMFILTER_FCURVESONLY, ac.data, ANIMCONT_ACTION));
    for (const bAnimListElem &ale : anim_data) {
      EXPECT_EQ(ANIMTYPE_FCURVE, ale.type);
      EXPECT_FALSE(ale.property_axis);
    }
    ANIM_animdata_freelist(&anim_data);

    /* The Graph Editor must keep the original channel list and names. */
    ac.spacetype = SPACE_GRAPH;
    EXPECT_EQ(4, ANIM_animdata_filter(&ac, &anim_data, filter, ac.data, ANIMCONT_ACTION));
    EXPECT_EQ(ANIMTYPE_FCURVE, anim_data.first()->next->type);
    EXPECT_FALSE(anim_data.first()->next->property_axis);
    ANIM_animdata_freelist(&anim_data);
    ac.spacetype = SPACE_ACTION;
  }

  { /* Test one expanded and one collapsed slot, and one Slot and one FCurve selected. */
    slot_cube.set_expanded(true);
    slot_cube.set_selected(false);
    slot_suzanne.set_expanded(false);
    slot_suzanne.set_selected(true);

    fcu_cube_loc_x->flag &= ~FCURVE_SELECTED;
    fcu_cube_loc_y->flag |= FCURVE_SELECTED;

    /* This should produce 1 slot and 1 FCurve. */
    ListBaseT<bAnimListElem> anim_data = {nullptr, nullptr};
    eAnimFilter_Flags filter = (ANIMFILTER_DATA_VISIBLE | ANIMFILTER_LIST_VISIBLE |
                                ANIMFILTER_SEL | ANIMFILTER_FOREDIT | ANIMFILTER_NODUPLIS |
                                ANIMFILTER_LIST_CHANNELS);
    const int num_entries = ANIM_animdata_filter(
        &ac, &anim_data, filter, ac.data, eAnimCont_Types(ac.datatype));
    EXPECT_EQ(2, num_entries);
    EXPECT_EQ(2, anim_data.count());

    /* First should summarize only Cube's selected FCurve. */
    const bAnimListElem *ale = static_cast<bAnimListElem *>(BLI_findlink(&anim_data, 0));
    EXPECT_EQ(ANIMTYPE_FCURVE_PROPERTY, ale->type);
    EXPECT_EQ(1, ale->property_curve_count);
    EXPECT_EQ(fcu_cube_loc_y, ale->data);

    /* Second the Suzanne slot, as that's the only selected slot. */
    ale = static_cast<bAnimListElem *>(BLI_findlink(&anim_data, 1));
    EXPECT_EQ(ANIMTYPE_ACTION_SLOT, ale->type);
    EXPECT_EQ(&slot_suzanne, ale->data);

    ANIM_animdata_freelist(&anim_data);
  }
}

TEST_F(ActionFilterTest, layered_action_active_fcurves)
{
  Slot &slot_cube = action->slot_add();
  /* The Action+Slot has to be assigned to what the bAnimContext thinks is the active Object.
   * See the BLI_assert_msg() call in the ANIMCONT_ACTION case of ANIM_animdata_filter(). */
  ASSERT_EQ(assign_action_and_slot(action, &slot_cube, cube->id), ActionSlotAssignmentResult::OK);

  Layer &layer = action->layer_add("Kübus layer");
  Strip &key_strip = layer.strip_add(*action, Strip::Type::Keyframe);
  StripKeyframeData &strip_data = key_strip.data<StripKeyframeData>(*action);

  /* Create multiple FCurves. */
  const KeyframeSettings settings = get_keyframe_settings(false);
  ASSERT_EQ(
      SingleKeyingResult::SUCCESS,
      strip_data.keyframe_insert(bmain, slot_cube, {"location", 0}, {1.0f, 0.25f}, settings));
  ASSERT_EQ(
      SingleKeyingResult::SUCCESS,
      strip_data.keyframe_insert(bmain, slot_cube, {"location", 1}, {1.0f, 0.25f}, settings));

  /* Set one F-Curve as the active one, and the other as inactive. The latter is necessary because
   * by default the first curve is automatically marked active, but that's too trivial a test case
   * (it's too easy to mistakenly just return the first-seen F-Curve). */
  Channelbag *cube_channelbag = strip_data.channelbag_for_slot(slot_cube);
  ASSERT_NE(nullptr, cube_channelbag);
  FCurve *fcurve_active = cube_channelbag->fcurve_find({"location", 1});
  fcurve_active->flag |= FCURVE_ACTIVE;
  FCurve *fcurve_other = cube_channelbag->fcurve_find({"location", 0});
  fcurve_other->flag &= ~FCURVE_ACTIVE;

  /* Mock an bAnimContext for the Action editor. */
  SpaceAction saction = {};
  saction.ads.filterflag = eDopeSheet_FilterFlag(0);

  bAnimContext ac = {nullptr};
  ac.bmain = bmain;
  ac.datatype = ANIMCONT_ACTION;
  ac.data = action;
  ac.spacetype = SPACE_ACTION;
  ac.sl = reinterpret_cast<SpaceLink *>(&saction);
  ac.obact = cube;
  ac.active_action = action;
  ac.active_action_user = &cube->id;
  ac.ads = &saction.ads;

  {
    /* This should produce just the active F-Curve. */
    ListBaseT<bAnimListElem> anim_data = {nullptr, nullptr};
    eAnimFilter_Flags filter = (ANIMFILTER_DATA_VISIBLE | ANIMFILTER_LIST_VISIBLE |
                                ANIMFILTER_FCURVESONLY | ANIMFILTER_ACTIVE);
    const int num_entries = ANIM_animdata_filter(
        &ac, &anim_data, filter, ac.data, eAnimCont_Types(ac.datatype));
    EXPECT_EQ(1, num_entries);
    EXPECT_EQ(1, anim_data.count());

    const bAnimListElem *first_ale = static_cast<bAnimListElem *>(BLI_findlink(&anim_data, 0));
    EXPECT_EQ(ANIMTYPE_FCURVE, first_ale->type);
    EXPECT_EQ(ALE_FCURVE, first_ale->datatype);
    EXPECT_EQ(fcurve_active, first_ale->data);

    ANIM_animdata_freelist(&anim_data);
  }
}

}  // namespace blender::animrig::tests

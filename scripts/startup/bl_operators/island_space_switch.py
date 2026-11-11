# SPDX-License-Identifier: GPL-2.0-or-later
"""Seamless Child Of constraint weight switching while preserving world transforms and animation."""

from __future__ import annotations

from math import acos, atan2, isfinite, sqrt

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup
from mathutils import Euler, Matrix, Quaternion, Vector


MODES = (
    ('CURRENT', '当前帧', '在当前帧切换子级约束权重并保持世界变换，自动保护切换前的动画不漂移'),
    ('RANGE', '帧范围保持动画', '在起止帧区间内切换权重并逐帧反解局部变换，完整保留整段世界空间动画轨迹'),
)

ACTIONS = (
    ('TOGGLE', '切换 0 ↔ 1', '在权重 0 与 1 之间切换，同时保持世界位置与旋转'),
    ('ENABLE', '跟随 (1)', '将此子级约束权重设为 1，同时保持当前世界位置与旋转'),
    ('DISABLE', '松开 (0)', '将此子级约束权重设为 0，同时保持当前世界位置与旋转'),
    ('EXCLUSIVE', '接管跟随', '将此子级约束设为 1，并将同一物体或骨骼上其余子级约束同步归 0，无损交接父级'),
    ('RELEASE_ALL', '全部松开 (0)', '将同一物体或骨骼上所有子级约束权重同步归 0，保持当前世界位置与旋转'),
    ('CUSTOM', '应用目标权重', '将此子级约束设为指定的目标权重（支持中间权重），同时保持世界位置与旋转'),
)

OWNER_TYPES = (
    ('AUTO', '自动', '根据当前约束上下文或编辑器模式自动识别物体或姿态骨骼'),
    ('OBJECT', '物体', '作用于活动物体的子级约束'),
    ('BONE', '姿态骨骼', '作用于活动姿态骨骼的子级约束'),
)

RANGE_SOURCES = (
    ('SELECTED_KEYS', '所选关键帧', '使用当前选中关键帧的最早与最晚帧作为起止帧'),
    ('SCENE', '场景帧范围', '使用当前场景或预览范围的起止帧'),
)

MAX_FRAME = 1048574
KEY_EPS = 1e-4
SOLVE_POS_TOL = 1e-6
SOLVE_ROT_TOL = 1e-6
SOLVE_SCALE_TOL = 1e-6
POS_TOL = 1e-4
ROT_TOL = 2e-4
SCALE_TOL = 1e-4

_SUPPRESS_AUTO_CHILDOF_UPDATE = False
_FORCE_PYTHON_AUTO_CHILDOF_SWITCH = False
_DRAG_WORLD_CACHE: dict[tuple, dict] = {}


class SpaceSwitchError(ValueError):
    pass


def _assigned_channelbag(owner_id):
    adt = getattr(owner_id, 'animation_data', None)
    if not adt or not adt.action or not adt.action_slot:
        return None
    for layer in adt.action.layers:
        for strip in layer.strips:
            if strip.type == 'KEYFRAME':
                bag = strip.channelbag(adt.action_slot)
                if bag is not None:
                    return bag
    return None


def _iter_assigned_fcurves(owner_id):
    bag = _assigned_channelbag(owner_id)
    if bag is not None:
        yield from bag.fcurves


def _find_fcurve(owner_id, data_path: str, array_index: int = 0):
    for fc in _iter_assigned_fcurves(owner_id):
        if fc.data_path == data_path and fc.array_index == array_index:
            return fc
    return None


def _has_driver(owner_id, data_path: str, array_index: int | None = None) -> bool:
    adt = getattr(owner_id, 'animation_data', None)
    if not adt:
        return False
    for drv in adt.drivers:
        if drv.data_path == data_path and (array_index is None or drv.array_index == array_index):
            return True
    return False


def _rotation_prop_info(owner):
    mode = owner.rotation_mode
    if mode == 'QUATERNION':
        return 'rotation_quaternion', 4
    if mode == 'AXIS_ANGLE':
        return 'rotation_axis_angle', 4
    return 'rotation_euler', 3


def resolve_owner_and_constraints(
    context,
    owner_type: str = 'AUTO',
    constraint_name: str = '',
    bone_name: str = '',
):
    """Return (ob, pchan_or_None, constraints_collection, target_constraint_or_None)."""
    ob = getattr(context, 'object', None) or getattr(context, 'active_object', None)
    if ob is None:
        return None, None, None, None

    ctx_con = getattr(context, 'constraint', None)
    if ctx_con is not None and not bone_name:
        ptr = ctx_con.as_pointer()
        if owner_type in {'AUTO', 'OBJECT'}:
            for con in ob.constraints:
                if con.as_pointer() == ptr:
                    if not constraint_name or con.name == constraint_name:
                        return ob, None, ob.constraints, con
        if owner_type in {'AUTO', 'BONE'} and ob.pose is not None:
            for pchan in ob.pose.bones:
                for con in pchan.constraints:
                    if con.as_pointer() == ptr:
                        if not constraint_name or con.name == constraint_name:
                            return ob, pchan, pchan.constraints, con

    space = getattr(context, 'space_data', None)
    space_ctx = getattr(space, 'context', '') if space and space.type == 'PROPERTIES' else ''

    pchan = None
    if bone_name:
        if ob.pose is None or bone_name not in ob.pose.bones:
            return ob, None, None, None
        pchan = ob.pose.bones[bone_name]
    elif owner_type == 'BONE':
        pchan = getattr(context, 'pose_bone', None) or getattr(context, 'active_pose_bone', None)
        if pchan is not None and pchan.id_data != ob:
            pchan = None
        if ob.pose is not None:
            need_fallback = (
                pchan is None
                or (constraint_name and constraint_name not in pchan.constraints)
                or (not constraint_name and not any(c.type == 'CHILD_OF' for c in pchan.constraints))
            )
            if need_fallback:
                selected_bones = [pb for pb in ob.pose.bones if pb.select]
                if constraint_name:
                    matches = [pb for pb in selected_bones if constraint_name in pb.constraints]
                else:
                    matches = [pb for pb in selected_bones if any(c.type == 'CHILD_OF' for c in pb.constraints)]
                if len(matches) == 1:
                    pchan = matches[0]
        if pchan is None:
            return ob, None, None, None
    elif owner_type == 'OBJECT':
        pchan = None
    else:
        if space_ctx == 'CONSTRAINT':
            pchan = None
        elif space_ctx == 'BONE_CONSTRAINT':
            pchan = getattr(context, 'pose_bone', None) or getattr(context, 'active_pose_bone', None)
        elif context.mode == 'POSE':
            cand = getattr(context, 'active_pose_bone', None) or getattr(context, 'pose_bone', None)
            if cand is not None and cand.id_data != ob:
                cand = None
            if ob.pose is not None:
                cand_has = cand is not None and (
                    (constraint_name and constraint_name in cand.constraints)
                    or (not constraint_name and any(c.type == 'CHILD_OF' for c in cand.constraints))
                )
                if not cand_has:
                    selected_bones = [pb for pb in ob.pose.bones if pb.select]
                    matches = [
                        pb
                        for pb in selected_bones
                        if (constraint_name and constraint_name in pb.constraints)
                        or (not constraint_name and any(c.type == 'CHILD_OF' for c in pb.constraints))
                    ]
                    if len(matches) == 1:
                        cand = matches[0]
            if cand is not None:
                if constraint_name and constraint_name not in cand.constraints and constraint_name in ob.constraints:
                    pchan = None
                elif not constraint_name and not any(c.type == 'CHILD_OF' for c in cand.constraints) and any(
                    c.type == 'CHILD_OF' for c in ob.constraints
                ):
                    pchan = None
                else:
                    pchan = cand

    constraints = pchan.constraints if pchan is not None else ob.constraints
    target_con = None
    if constraint_name:
        target_con = constraints.get(constraint_name)
    elif ctx_con is not None and ctx_con.type == 'CHILD_OF':
        for con in constraints:
            if con.as_pointer() == ctx_con.as_pointer():
                target_con = con
                break
    if target_con is None and not constraint_name:
        childofs = [c for c in constraints if c.type == 'CHILD_OF']
        if len(childofs) == 1:
            target_con = childofs[0]
        else:
            valid_childofs = [c for c in childofs if not c.mute and c.target is not None]
            if len(valid_childofs) == 1:
                target_con = valid_childofs[0]
    return ob, pchan, constraints, target_con


def _validate_childof(con):
    if con is None or con.type != 'CHILD_OF':
        raise SpaceSwitchError('请指定有效的子级约束（Child Of）')
    if con.mute:
        raise SpaceSwitchError(f'子级约束“{con.name}”已关闭可见性（眼睛图标），请先开启后再切换权重')
    if con.target is None:
        raise SpaceSwitchError(f'子级约束“{con.name}”尚未选择目标物体')
    if con.target.type == 'ARMATURE' and con.subtarget:
        if con.target.pose is None or con.subtarget not in con.target.pose.bones:
            raise SpaceSwitchError(f'子级约束“{con.name}”的目标骨骼“{con.subtarget}”不存在')


def _validate_owner_animation_editable(ob, pchan, switched_constraints):
    if not ob.is_editable:
        raise SpaceSwitchError('当前物体为外部关联只读数据，请先创建库重写（Library Override）后再切换')
    adt = ob.animation_data
    if adt is not None:
        if adt.use_tweak_mode:
            raise SpaceSwitchError('请先退出 NLA 微调模式，再执行子级约束空间切换')
        if adt.action is not None and not adt.action.is_editable:
            raise SpaceSwitchError('当前活动动作不可编辑，请先新建或指定本地可编辑动作后再切换')

    owner = pchan if pchan is not None else ob
    rot_prop, rot_len = _rotation_prop_info(owner)
    transform_paths = [
        (owner.path_from_id('location'), 3),
        (owner.path_from_id(rot_prop), rot_len),
        (owner.path_from_id('scale'), 3),
    ]
    for path, length in transform_paths:
        if _has_driver(ob, path):
            raise SpaceSwitchError('物体的变换通道存在驱动器，无法直接写入空间补偿关键帧')
        for idx in range(length):
            fc = _find_fcurve(ob, path, idx)
            if fc is not None:
                if fc.lock or (fc.group and fc.group.lock):
                    raise SpaceSwitchError('物体的变换动画曲线或通道组已锁定，请解锁后再切换')
                if len(fc.sampled_points) > 0:
                    raise SpaceSwitchError('物体的变换曲线为采样点数据，请先转换为关键帧后再切换')
                if any(not mod.mute for mod in fc.modifiers):
                    raise SpaceSwitchError('物体的变换曲线含启用中的曲线修改器，请先关闭或应用后再切换')

    for con in switched_constraints:
        inf_path = con.path_from_id('influence')
        if _has_driver(ob, inf_path):
            raise SpaceSwitchError(f'子级约束“{con.name}”的影响权重受驱动器控制，无法切换')
        fc = _find_fcurve(ob, inf_path, 0)
        if fc is not None:
            if fc.lock or (fc.group and fc.group.lock):
                raise SpaceSwitchError(f'子级约束“{con.name}”的影响权重曲线已锁定，请解锁后再切换')
            if len(fc.sampled_points) > 0:
                raise SpaceSwitchError(f'子级约束“{con.name}”的影响权重曲线为采样点数据，无法直接切换')
            if any(not mod.mute for mod in fc.modifiers):
                raise SpaceSwitchError(f'子级约束“{con.name}”的影响权重曲线含启用中的修改器，无法直接切换')


def _set_frame_exact(scene, frame_value: float):
    whole = int(frame_value // 1.0)
    sub = float(frame_value - whole)
    if abs(sub - 1.0) < 1e-7:
        whole += 1
        sub = 0.0
    elif abs(sub) < 1e-7:
        sub = 0.0
    scene.frame_set(whole, subframe=sub)


def _evaluated_world_matrix(context, ob, bone_name: str | None) -> Matrix:
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    ev_ob = ob.evaluated_get(depsgraph)
    if bone_name is not None:
        return (ev_ob.matrix_world @ ev_ob.pose.bones[bone_name].matrix).copy()
    return ev_ob.matrix_world.copy()


def _evaluated_target_matrix(context, con) -> Matrix:
    depsgraph = context.evaluated_depsgraph_get()
    ev_target = con.target.evaluated_get(depsgraph)
    if con.target.type == 'ARMATURE' and con.subtarget and ev_target.pose and con.subtarget in ev_target.pose.bones:
        return (ev_target.matrix_world @ ev_target.pose.bones[con.subtarget].matrix).copy()
    return ev_target.matrix_world.copy()


def _capture_local_state(owner):
    return {
        'location': Vector(owner.location),
        'rotation_euler': Euler(owner.rotation_euler, owner.rotation_mode if owner.rotation_mode not in {'QUATERNION', 'AXIS_ANGLE'} else 'XYZ'),
        'rotation_quaternion': Quaternion(owner.rotation_quaternion),
        'rotation_axis_angle': tuple(float(v) for v in owner.rotation_axis_angle),
        'scale': Vector(owner.scale),
    }


def _restore_local_state(owner, state):
    owner.location = state['location']
    owner.rotation_euler = state['rotation_euler']
    owner.rotation_quaternion = state['rotation_quaternion']
    owner.rotation_axis_angle = state['rotation_axis_angle']
    owner.scale = state['scale']


def _owner_current_quat(owner) -> Quaternion:
    mode = owner.rotation_mode
    if mode == 'QUATERNION':
        q = Quaternion(owner.rotation_quaternion)
        return q.normalized() if q.magnitude > 1e-12 else Quaternion((1.0, 0.0, 0.0, 0.0))
    if mode == 'AXIS_ANGLE':
        aa = owner.rotation_axis_angle
        return Quaternion(Vector(aa[1:4]), float(aa[0])).normalized()
    return owner.rotation_euler.to_quaternion()


def _is_connected_bone(ob, pchan) -> bool:
    return bool(pchan is not None and pchan.bone is not None and pchan.bone.use_connect)


def _any_rotation_locked(owner) -> bool:
    if any(owner.lock_rotation):
        return True
    if owner.rotation_mode in {'QUATERNION', 'AXIS_ANGLE'} and owner.lock_rotations_4d and owner.lock_rotation_w:
        return True
    return False


def _apply_rotation_quat(owner, quat: Quaternion, ref_euler: Euler | None = None, ref_quat: Quaternion | None = None):
    q = quat.normalized()
    mode = owner.rotation_mode
    if mode == 'QUATERNION':
        if ref_quat is not None and q.dot(ref_quat) < 0.0:
            q = -q
        if owner.lock_rotations_4d:
            cur = list(owner.rotation_quaternion)
            cand = [q.w, q.x, q.y, q.z]
            if not owner.lock_rotation_w:
                cur[0] = cand[0]
            for i in range(3):
                if not owner.lock_rotation[i]:
                    cur[i + 1] = cand[i + 1]
            owner.rotation_quaternion = cur
        elif any(owner.lock_rotation):
            cur_eul = _owner_current_quat(owner).to_euler('XYZ')
            cand_eul = q.to_euler('XYZ', cur_eul)
            for i in range(3):
                if not owner.lock_rotation[i]:
                    cur_eul[i] = cand_eul[i]
            q_locked = cur_eul.to_quaternion()
            if ref_quat is not None and q_locked.dot(ref_quat) < 0.0:
                q_locked = -q_locked
            owner.rotation_quaternion = q_locked
        else:
            owner.rotation_quaternion = q
    elif mode == 'AXIS_ANGLE':
        axis, angle = q.to_axis_angle()
        if owner.lock_rotations_4d:
            cur = list(owner.rotation_axis_angle)
            cand = [angle, axis.x, axis.y, axis.z]
            if not owner.lock_rotation_w:
                cur[0] = cand[0]
            for i in range(3):
                if not owner.lock_rotation[i]:
                    cur[i + 1] = cand[i + 1]
            owner.rotation_axis_angle = cur
        elif any(owner.lock_rotation):
            cur_eul = _owner_current_quat(owner).to_euler('XYZ')
            cand_eul = q.to_euler('XYZ', cur_eul)
            for i in range(3):
                if not owner.lock_rotation[i]:
                    cur_eul[i] = cand_eul[i]
            axis2, angle2 = cur_eul.to_quaternion().to_axis_angle()
            owner.rotation_axis_angle = (angle2, axis2.x, axis2.y, axis2.z)
        else:
            owner.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
    else:
        compat = ref_euler if ref_euler is not None else owner.rotation_euler
        eul = q.to_matrix().to_euler(mode, compat)
        for i in range(3):
            if not owner.lock_rotation[i]:
                owner.rotation_euler[i] = eul[i]


def _world_residual(world: Matrix, target: Matrix, include_scale: bool):
    offset = world.translation - target.translation
    q_cur = world.to_quaternion().normalized()
    q_tar = target.to_quaternion().normalized()
    delta = (q_cur @ q_tar.inverted()).normalized()
    if delta.w < 0.0:
        delta = -delta
    vec = Vector((delta.x, delta.y, delta.z))
    sine = vec.length
    factor = 2.0 * atan2(sine, delta.w) / sine if sine > 1e-8 else 2.0
    rot_err = vec * factor
    if include_scale:
        s_diff = world.to_scale() - target.to_scale()
        return [
            float(offset.x), float(offset.y), float(offset.z),
            float(rot_err.x), float(rot_err.y), float(rot_err.z),
            float(s_diff.x), float(s_diff.y), float(s_diff.z),
        ]
    return [
        float(offset.x), float(offset.y), float(offset.z),
        float(rot_err.x), float(rot_err.y), float(rot_err.z),
    ]


def _residual_matches(
    residual,
    include_scale: bool,
    pos_tol: float = POS_TOL,
    rot_tol: float = ROT_TOL,
    scale_tol: float = SCALE_TOL,
) -> bool:
    pos_sq = residual[0] ** 2 + residual[1] ** 2 + residual[2] ** 2
    rot_sq = residual[3] ** 2 + residual[4] ** 2 + residual[5] ** 2
    if pos_sq > pos_tol ** 2 or rot_sq > rot_tol ** 2:
        return False
    if include_scale:
        scale_sq = residual[6] ** 2 + residual[7] ** 2 + residual[8] ** 2
        if scale_sq > scale_tol ** 2:
            return False
    return True


def _solve_linear_system(matrix, rhs):
    """Gaussian elimination with partial pivoting; returns solution list or None."""
    n = len(rhs)
    aug = [list(matrix[r]) + [rhs[r]] for r in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-15:
            return None
        if pivot != col:
            aug[col], aug[pivot] = aug[pivot], aug[col]
        div = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= div
        for r in range(n):
            if r != col:
                factor = aug[r][col]
                if factor != 0.0:
                    for j in range(col, n + 1):
                        aug[r][j] -= factor * aug[col][j]
    sol = [aug[i][n] for i in range(n)]
    return sol if all(isfinite(v) for v in sol) else None


def _solve_owner_world_matrix(
    context,
    ob,
    pchan,
    desired_world: Matrix,
    ref_euler: Euler | None = None,
    ref_quat: Quaternion | None = None,
):
    """Solve owner's local channels so evaluated world matrix matches desired_world.
    Returns (solved_local_state, scale_compensated) or raises SpaceSwitchError.
    """
    owner = pchan if pchan is not None else ob
    bone_name = pchan.name if pchan is not None else None
    connected = _is_connected_bone(ob, pchan)

    cur_world = _evaluated_world_matrix(context, ob, bone_name)
    desired_scale = desired_world.to_scale()
    need_scale = (cur_world.to_scale() - desired_scale).length > 1e-4

    # 1. Fast right-preconditioned matrix passes (exact in 1 step for full 0/1 Child Of stacks).
    for _ in range(3):
        res = _world_residual(cur_world, desired_world, need_scale)
        if _residual_matches(res, need_scale, SOLVE_POS_TOL, SOLVE_ROT_TOL, SOLVE_SCALE_TOL):
            break
        det = cur_world.determinant()
        if abs(det) <= 1e-12:
            break
        basis_cur = owner.matrix_basis.copy()
        basis_next = basis_cur @ cur_world.inverted() @ desired_world
        loc, quat, sca = basis_next.decompose()
        if not connected:
            for i in range(3):
                if not owner.lock_location[i]:
                    owner.location[i] = loc[i]
        _apply_rotation_quat(owner, quat, ref_euler=ref_euler, ref_quat=ref_quat)
        if need_scale:
            for i in range(3):
                if not owner.lock_scale[i]:
                    owner.scale[i] = sca[i]
        cur_world = _evaluated_world_matrix(context, ob, bone_name)

    # Re-check whether scale actually needs compensation after loc/rot adjustment.
    if not need_scale and (cur_world.to_scale() - desired_scale).length > 1e-5:
        need_scale = True

    res = _world_residual(cur_world, desired_world, need_scale)
    if not _residual_matches(res, need_scale, SOLVE_POS_TOL, SOLVE_ROT_TOL, SOLVE_SCALE_TOL):
        # 2. Damped Gauss-Newton (Levenberg-Marquardt) on unlocked local DOFs.
        dofs = []
        if not connected:
            for i in range(3):
                if not owner.lock_location[i]:
                    dofs.append(('LOC', i))
        if not _any_rotation_locked(owner):
            for i in range(3):
                dofs.append(('ROT_TAN', i))
        elif owner.rotation_mode not in {'QUATERNION', 'AXIS_ANGLE'}:
            for i in range(3):
                if not owner.lock_rotation[i]:
                    dofs.append(('ROT_EUL', i))
        else:
            for i in range(3):
                if not owner.lock_rotation[i]:
                    dofs.append(('ROT_LOCKED_AXIS', i))
        if need_scale:
            for i in range(3):
                if not owner.lock_scale[i]:
                    dofs.append(('SCALE', i))

        def apply_step(base_state, base_quat, step_vec):
            _restore_local_state(owner, base_state)
            rot_tan = [0.0, 0.0, 0.0]
            has_rot_tan = False
            rot_locked_eul = None
            for (kind, axis), val in zip(dofs, step_vec):
                if kind == 'LOC':
                    owner.location[axis] = base_state['location'][axis] + val
                elif kind == 'ROT_TAN':
                    rot_tan[axis] = val
                    has_rot_tan = True
                elif kind == 'ROT_EUL':
                    owner.rotation_euler[axis] = base_state['rotation_euler'][axis] + val
                elif kind == 'ROT_LOCKED_AXIS':
                    if rot_locked_eul is None:
                        rot_locked_eul = base_quat.to_euler('XYZ')
                    rot_locked_eul[axis] += val
                elif kind == 'SCALE':
                    owner.scale[axis] = base_state['scale'][axis] + val
            if has_rot_tan:
                axis_vec = Vector(rot_tan)
                angle = axis_vec.length
                if angle > 0.0:
                    dq = Quaternion(axis_vec / angle, angle)
                    _apply_rotation_quat(owner, (dq @ base_quat).normalized(), ref_euler=ref_euler, ref_quat=ref_quat)
            elif rot_locked_eul is not None:
                _apply_rotation_quat(owner, rot_locked_eul.to_quaternion(), ref_euler=ref_euler, ref_quat=ref_quat)

        if dofs:
            damping = 1e-4
            n_dofs = len(dofs)
            for _ in range(28):
                cur_world = _evaluated_world_matrix(context, ob, bone_name)
                res = _world_residual(cur_world, desired_world, need_scale)
                if _residual_matches(res, need_scale, SOLVE_POS_TOL, SOLVE_ROT_TOL, SOLVE_SCALE_TOL):
                    break
                old_cost = sum(c * c for c in res)
                if not isfinite(old_cost):
                    break
                base_state = _capture_local_state(owner)
                base_quat = _owner_current_quat(owner)
                m_rows = len(res)
                jac = [[0.0] * n_dofs for _ in range(m_rows)]
                for col, (kind, axis) in enumerate(dofs):
                    step = [0.0] * n_dofs
                    if kind == 'LOC':
                        eps = 1e-4 * max(1.0, abs(base_state['location'][axis]))
                    elif kind == 'SCALE':
                        eps = 1e-4 * max(1.0, abs(base_state['scale'][axis]))
                    else:
                        eps = 1e-4
                    step[col] = eps
                    apply_step(base_state, base_quat, step)
                    pert_world = _evaluated_world_matrix(context, ob, bone_name)
                    pert_res = _world_residual(pert_world, desired_world, need_scale)
                    for r in range(m_rows):
                        jac[r][col] = (pert_res[r] - res[r]) / eps
                _restore_local_state(owner, base_state)

                improved = False
                for _ in range(8):
                    ata = [[0.0] * n_dofs for _ in range(n_dofs)]
                    atb = [0.0] * n_dofs
                    for i in range(n_dofs):
                        for j in range(n_dofs):
                            ata[i][j] = sum(jac[r][i] * jac[r][j] for r in range(m_rows))
                        ata[i][i] += damping * max(ata[i][i], 1e-6)
                        atb[i] = -sum(jac[r][i] * res[r] for r in range(m_rows))
                    delta_step = _solve_linear_system(ata, atb)
                    if delta_step is None:
                        damping *= 10.0
                        continue
                    rot_norm = sqrt(sum(
                        v * v for (k, _), v in zip(dofs, delta_step)
                        if k in {'ROT_TAN', 'ROT_EUL', 'ROT_LOCKED_AXIS'}
                    ))
                    if rot_norm > 1.0:
                        delta_step = [v / rot_norm for v in delta_step]
                    apply_step(base_state, base_quat, delta_step)
                    cand_world = _evaluated_world_matrix(context, ob, bone_name)
                    cand_res = _world_residual(cand_world, desired_world, need_scale)
                    if sum(c * c for c in cand_res) < old_cost:
                        damping = max(damping * 0.25, 1e-8)
                        improved = True
                        break
                    damping *= 10.0
                    _restore_local_state(owner, base_state)
                if not improved:
                    break

    # Finalize continuous Euler / Quaternion representation and verify world residual.
    final_quat = _owner_current_quat(owner)
    _apply_rotation_quat(owner, final_quat, ref_euler=ref_euler, ref_quat=ref_quat)
    final_world = _evaluated_world_matrix(context, ob, bone_name)
    final_res = _world_residual(final_world, desired_world, need_scale)
    if not _residual_matches(final_res, need_scale):
        if connected and (final_res[0] ** 2 + final_res[1] ** 2 + final_res[2] ** 2 > POS_TOL ** 2):
            raise SpaceSwitchError(
                '相连骨骼的头部受父骨骼尾部约束，无法在新权重下保持偏移后的世界位置；请关闭子级约束的位置轴或断开骨骼连接'
            )
        raise SpaceSwitchError(
            '无法在新权重下保持当前世界变换，未修改动画；请检查变换轴锁定、零缩放或后续覆盖型约束'
        )
    return _capture_local_state(owner), need_scale


def _find_key(fc, frame: float):
    for key in fc.keyframe_points:
        if abs(float(key.co.x) - frame) <= KEY_EPS:
            return key
    return None


def _correct_bezpart(v1, v2, v3, v4):
    span = v4[0] - v1[0]
    len1 = abs(v1[0] - v2[0])
    len2 = abs(v4[0] - v3[0])
    if span <= 0.0 or (len1 + len2) == 0.0:
        return v2, v3
    v2 = list(v2)
    v3 = list(v3)
    if len1 > span:
        fac = span / len1
        v2[0] = v1[0] - fac * (v1[0] - v2[0])
        v2[1] = v1[1] - fac * (v1[1] - v2[1])
    if len2 > span:
        fac = span / len2
        v3[0] = v4[0] - fac * (v4[0] - v3[0])
        v3[1] = v4[1] - fac * (v4[1] - v3[1])
    return tuple(v2), tuple(v3)


def _lerp2(a, b, t: float):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _capture_bezier_split_info(fc, split_x: float):
    """Capture exact De Casteljau handle data before inserting a boundary key at split_x."""
    if fc is None or len(fc.keyframe_points) == 0:
        return None
    keys = fc.keyframe_points
    existing = _find_key(fc, split_x)
    if existing is not None:
        idx = next(i for i, k in enumerate(keys) if k == existing)
        prev_info = None
        next_info = None
        if idx > 0 and keys[idx - 1].interpolation == 'BEZIER':
            pk = keys[idx - 1]
            prev_info = (
                float(pk.co.x),
                (float(pk.handle_left.x), float(pk.handle_left.y)),
                (float(pk.handle_right.x), float(pk.handle_right.y)),
            )
        if idx + 1 < len(keys):
            nk = keys[idx + 1]
            next_info = (
                float(nk.co.x),
                (float(nk.handle_left.x), float(nk.handle_left.y)),
                (float(nk.handle_right.x), float(nk.handle_right.y)),
            )
        return {
            'existed': True,
            'prev': prev_info,
            'split_hl': (float(existing.handle_left.x), float(existing.handle_left.y)),
            'split_hr': (float(existing.handle_right.x), float(existing.handle_right.y)),
            'split_ipo': existing.interpolation,
            'next': next_info,
        }

    for idx in range(len(keys) - 1):
        k0 = keys[idx]
        k1 = keys[idx + 1]
        x0 = float(k0.co.x)
        x1 = float(k1.co.x)
        if x0 < split_x < x1:
            if k0.interpolation != 'BEZIER':
                return {
                    'existed': False,
                    'prev': None,
                    'split_hl': None,
                    'split_hr': None,
                    'split_ipo': k0.interpolation,
                    'next': None,
                }
            v1 = (x0, float(k0.co.y))
            v2 = (float(k0.handle_right.x), float(k0.handle_right.y))
            v3 = (float(k1.handle_left.x), float(k1.handle_left.y))
            v4 = (x1, float(k1.co.y))
            v2c, v3c = _correct_bezpart(v1, v2, v3, v4)
            lo, hi = 0.0, 1.0
            for _ in range(64):
                mid = 0.5 * (lo + hi)
                mt = 1.0 - mid
                bx = (
                    mt * mt * mt * v1[0]
                    + 3.0 * mt * mt * mid * v2c[0]
                    + 3.0 * mt * mid * mid * v3c[0]
                    + mid * mid * mid * v4[0]
                )
                if bx < split_x:
                    lo = mid
                else:
                    hi = mid
            u = 0.5 * (lo + hi)
            p01 = _lerp2(v1, v2c, u)
            p12 = _lerp2(v2c, v3c, u)
            p23 = _lerp2(v3c, v4, u)
            p012 = _lerp2(p01, p12, u)
            p123 = _lerp2(p12, p23, u)
            return {
                'existed': False,
                'prev': (x0, (float(k0.handle_left.x), float(k0.handle_left.y)), p01),
                'split_hl': p012,
                'split_hr': p123,
                'split_ipo': 'BEZIER',
                'next': (x1, p23, (float(k1.handle_right.x), float(k1.handle_right.y))),
            }
    return None


def _ensure_fcurve(ob, data_path: str, array_index: int, is_array: bool, group_name: str, seed_frame: float):
    fc = _find_fcurve(ob, data_path, array_index if is_array else 0)
    if fc is not None:
        return fc
    kwargs = {'frame': seed_frame}
    if group_name:
        kwargs['group'] = group_name
    if is_array:
        ob.keyframe_insert(data_path=data_path, index=array_index, **kwargs)
    else:
        ob.keyframe_insert(data_path=data_path, **kwargs)
    fc = _find_fcurve(ob, data_path, array_index if is_array else 0)
    if fc is None:
        raise SpaceSwitchError(f'无法创建动画曲线：{data_path}')
    return fc


def _upsert_key(fc, frame: float, value: float, key_type: str = 'KEYFRAME'):
    k = _find_key(fc, frame)
    if k is None:
        k = fc.keyframe_points.insert(frame, value, options={'FAST'})
    else:
        k.co.x = frame
        k.co.y = value
    k.type = key_type
    return k


def _has_any_keys_before(ob, constraints, frame: float) -> bool:
    for fc in _iter_assigned_fcurves(ob):
        for k in fc.keyframe_points:
            if float(k.co.x) < frame - KEY_EPS:
                return True
    for con in constraints:
        if con.type == 'CHILD_OF' and con.target is not None:
            for fc in _iter_assigned_fcurves(con.target):
                for k in fc.keyframe_points:
                    if float(k.co.x) < frame - KEY_EPS:
                        return True
    return False


def _has_any_keys_after(ob, constraints, frame: float) -> bool:
    for fc in _iter_assigned_fcurves(ob):
        for k in fc.keyframe_points:
            if float(k.co.x) > frame + KEY_EPS:
                return True
    for con in constraints:
        if con.type == 'CHILD_OF' and con.target is not None:
            for fc in _iter_assigned_fcurves(con.target):
                for k in fc.keyframe_points:
                    if float(k.co.x) > frame + KEY_EPS:
                        return True
    return False


def _state_channel_value(state, prop_name: str, index: int) -> float:
    return float(state[prop_name][index])


def _collect_owner_channel_specs(ob, pchan, include_scale: bool):
    owner = pchan if pchan is not None else ob
    group_name = pchan.name if pchan is not None else 'Object Transforms'
    specs = []
    connected = _is_connected_bone(ob, pchan)
    loc_path = owner.path_from_id('location')
    if not connected or any(_find_fcurve(ob, loc_path, i) is not None for i in range(3)):
        for i in range(3):
            if not owner.lock_location[i] or _find_fcurve(ob, loc_path, i) is not None:
                specs.append((loc_path, i, True, group_name, 'location'))

    rot_prop, rot_len = _rotation_prop_info(owner)
    rot_path = owner.path_from_id(rot_prop)
    for i in range(rot_len):
        specs.append((rot_path, i, True, group_name, rot_prop))

    scale_path = owner.path_from_id('scale')
    has_scale_curves = any(_find_fcurve(ob, scale_path, i) is not None for i in range(3))
    if include_scale or has_scale_curves:
        for i in range(3):
            if not owner.lock_scale[i] or _find_fcurve(ob, scale_path, i) is not None:
                specs.append((scale_path, i, True, group_name, 'scale'))
    return specs


def _write_current_mode_curves(
    ob,
    pchan,
    switched_constraints,
    target_weights: dict[str, float],
    frame_t: float,
    solved_t,
    should_guard: bool,
    frame_prev: float,
    state_prev,
    influences_prev: dict[str, float],
    include_scale: bool,
):
    group_name = pchan.name if pchan is not None else ''
    # 1. Constraint influence curves (always stepped CONSTANT on switches).
    for con in switched_constraints:
        inf_path = con.path_from_id('influence')
        fc = _ensure_fcurve(ob, inf_path, 0, False, group_name, frame_t)
        if should_guard:
            k_prev = _upsert_key(fc, frame_prev, influences_prev[con.name])
            k_prev.interpolation = 'CONSTANT'
        k_cur = _upsert_key(fc, frame_t, target_weights[con.name])
        k_cur.interpolation = 'CONSTANT'
        fc.update()

    # 2. Owner transform curves with exact left-curve preservation and isolated switch handles.
    specs = _collect_owner_channel_specs(ob, pchan, include_scale)
    for data_path, idx, is_array, grp, state_key in specs:
        existing_fc = _find_fcurve(ob, data_path, idx)
        split_prev = _capture_bezier_split_info(existing_fc, frame_prev) if should_guard else None
        fc = existing_fc or _ensure_fcurve(ob, data_path, idx, is_array, grp, frame_t)

        val_t = _state_channel_value(solved_t, state_key, idx)
        if should_guard:
            val_prev = _state_channel_value(state_prev, state_key, idx)
            _upsert_key(fc, frame_prev, val_prev)
        _upsert_key(fc, frame_t, val_t)
        fc.update()

        if should_guard:
            if split_prev and split_prev['prev'] is not None:
                px, phl, phr = split_prev['prev']
                pk = _find_key(fc, px)
                if pk is not None and abs(px - frame_t) > KEY_EPS:
                    pk.handle_left_type = 'FREE'
                    pk.handle_right_type = 'FREE'
                    pk.handle_left = phl
                    pk.handle_right = phr

            k_guard = _find_key(fc, frame_prev)
            if k_guard is not None:
                k_guard.interpolation = 'CONSTANT'
                k_guard.handle_left_type = 'FREE'
                k_guard.handle_right_type = 'FREE'
                if split_prev and split_prev['split_hl'] is not None:
                    k_guard.handle_left = split_prev['split_hl']
                else:
                    k_guard.handle_left = (frame_prev - 1.0 / 3.0, val_prev)
                k_guard.handle_right = (frame_prev + 1.0 / 3.0, val_prev)

            k_switch = _find_key(fc, frame_t)
            if k_switch is not None:
                keys = fc.keyframe_points
                s_idx = next((i for i, k in enumerate(keys) if k == k_switch), -1)
                dx_r = 1.0 / 3.0
                if s_idx >= 0 and s_idx + 1 < len(keys):
                    dx_r = min(1.0, max(0.01, float(keys[s_idx + 1].co.x) - frame_t)) / 3.0
                if k_switch.interpolation not in {'CONSTANT', 'LINEAR'}:
                    k_switch.interpolation = 'BEZIER'
                k_switch.handle_left_type = 'FREE'
                k_switch.handle_right_type = 'FREE'
                k_switch.handle_left = (frame_t - 1.0 / 3.0, val_t)
                k_switch.handle_right = (frame_t + dx_r, val_t)
            fc.update()


def _remove_keys_in_closed_interval(fc, start_x: float, end_x: float):
    for idx in range(len(fc.keyframe_points) - 1, -1, -1):
        kx = float(fc.keyframe_points[idx].co.x)
        if start_x - KEY_EPS <= kx <= end_x + KEY_EPS:
            fc.keyframe_points.remove(fc.keyframe_points[idx], fast=True)


def _write_range_mode_curves(
    ob,
    pchan,
    switched_constraints,
    target_weights: dict[str, float],
    f_start: int,
    f_end: int,
    solved_by_frame: dict[int, dict],
    guard_before: bool,
    state_before,
    influences_before: dict[str, float],
    guard_after: bool,
    state_after,
    influences_after: dict[str, float],
    include_scale: bool,
):
    group_name = pchan.name if pchan is not None else ''
    f_prev = float(f_start - 1)
    f_next = float(f_end + 1)

    # 1. Constraint influence curves.
    for con in switched_constraints:
        inf_path = con.path_from_id('influence')
        fc = _ensure_fcurve(ob, inf_path, 0, False, group_name, float(f_start))
        _remove_keys_in_closed_interval(fc, float(f_start), float(f_end))
        if guard_before:
            kb = _upsert_key(fc, f_prev, influences_before[con.name])
            kb.interpolation = 'CONSTANT'
        ks = _upsert_key(fc, float(f_start), target_weights[con.name])
        ks.interpolation = 'CONSTANT'
        if f_end > f_start:
            ke = _upsert_key(fc, float(f_end), target_weights[con.name])
            ke.interpolation = 'CONSTANT'
        if guard_after:
            ka = _upsert_key(fc, f_next, influences_after[con.name])
            ka.interpolation = 'CONSTANT'
        fc.update()

    # 2. Owner transform curves across [f_start, f_end] with boundary guards.
    specs = _collect_owner_channel_specs(ob, pchan, include_scale)
    for data_path, idx, is_array, grp, state_key in specs:
        existing_fc = _find_fcurve(ob, data_path, idx)
        split_before = _capture_bezier_split_info(existing_fc, f_prev) if guard_before else None
        split_after = _capture_bezier_split_info(existing_fc, f_next) if guard_after else None
        fc = existing_fc or _ensure_fcurve(ob, data_path, idx, is_array, grp, float(f_start))

        _remove_keys_in_closed_interval(fc, float(f_start), float(f_end))

        if guard_before:
            val_before = _state_channel_value(state_before, state_key, idx)
            _upsert_key(fc, f_prev, val_before)
        if guard_after:
            val_after = _state_channel_value(state_after, state_key, idx)
            _upsert_key(fc, f_next, val_after)

        for frame in range(f_start, f_end + 1):
            val_f = _state_channel_value(solved_by_frame[frame], state_key, idx)
            k = _upsert_key(fc, float(frame), val_f, key_type='KEYFRAME' if frame in {f_start, f_end} else 'BREAKDOWN')
            k.interpolation = 'BEZIER'
            k.handle_left_type = 'AUTO_CLAMPED'
            k.handle_right_type = 'AUTO_CLAMPED'

        fc.update()

        if guard_before:
            if split_before and split_before['prev'] is not None:
                px, phl, phr = split_before['prev']
                pk = _find_key(fc, px)
                if pk is not None and px < f_prev - KEY_EPS:
                    pk.handle_left_type = 'FREE'
                    pk.handle_right_type = 'FREE'
                    pk.handle_left = phl
                    pk.handle_right = phr
            kb = _find_key(fc, f_prev)
            if kb is not None:
                val_b = float(kb.co.y)
                kb.interpolation = 'CONSTANT'
                kb.handle_left_type = 'FREE'
                kb.handle_right_type = 'FREE'
                kb.handle_left = (
                    split_before['split_hl']
                    if split_before and split_before['split_hl'] is not None
                    else (f_prev - 1.0 / 3.0, val_b)
                )
                kb.handle_right = (f_prev + 1.0 / 3.0, val_b)

        ks = _find_key(fc, float(f_start))
        if ks is not None:
            val_s = float(ks.co.y)
            hr = (float(ks.handle_right.x), float(ks.handle_right.y))
            ks.handle_left_type = 'FREE'
            ks.handle_right_type = 'FREE'
            ks.handle_left = (float(f_start) - 1.0 / 3.0, val_s)
            if f_end > f_start:
                ks.handle_right = hr
            else:
                ks.handle_right = (float(f_start) + 1.0 / 3.0, val_s)

        ke = _find_key(fc, float(f_end))
        if ke is not None:
            val_e = float(ke.co.y)
            hl = (float(ke.handle_left.x), float(ke.handle_left.y))
            ke.handle_left_type = 'FREE'
            ke.handle_right_type = 'FREE'
            if f_end > f_start:
                ke.handle_left = hl
            else:
                ke.handle_left = (float(f_end) - 1.0 / 3.0, val_e)
            ke.handle_right = (float(f_end) + 1.0 / 3.0, val_e)
            if guard_after:
                ke.interpolation = 'CONSTANT'

        if guard_after:
            if split_after and split_after['next'] is not None:
                nx, nhl, nhr = split_after['next']
                nk = _find_key(fc, nx)
                if nk is not None and nx > f_next + KEY_EPS:
                    nk.handle_left_type = 'FREE'
                    nk.handle_right_type = 'FREE'
                    nk.handle_left = nhl
                    nk.handle_right = nhr
            ka = _find_key(fc, f_next)
            if ka is not None:
                val_a = float(ka.co.y)
                ka.handle_left_type = 'FREE'
                ka.handle_right_type = 'FREE'
                ka.handle_left = (f_next - 1.0 / 3.0, val_a)
                ka.handle_right = (
                    split_after['split_hr']
                    if split_after and split_after['split_hr'] is not None
                    else (f_next + 1.0 / 3.0, val_a)
                )
                if split_after and split_after['split_ipo']:
                    ka.interpolation = split_after['split_ipo']

        fc.update()


def _build_target_weights(constraints, target_con, action: str, custom_weight: float):
    active_childofs = [c for c in constraints if c.type == 'CHILD_OF' and not c.mute]
    if action == 'RELEASE_ALL':
        valid_childofs = [c for c in active_childofs if c.target is not None]
        if not valid_childofs:
            raise SpaceSwitchError('当前物体或骨骼没有启用中的有效子级约束')
        for c in valid_childofs:
            _validate_childof(c)
        return valid_childofs, {c.name: 0.0 for c in valid_childofs}

    _validate_childof(target_con)
    if action == 'EXCLUSIVE':
        switched = []
        weights = {}
        for c in active_childofs:
            if c == target_con:
                switched.append(c)
                weights[c.name] = 1.0
            elif c.target is not None:
                _validate_childof(c)
                switched.append(c)
                weights[c.name] = 0.0
        return switched, weights

    if action == 'TOGGLE':
        w = 0.0 if target_con.influence > 0.5 else 1.0
    elif action == 'ENABLE':
        w = 1.0
    elif action == 'DISABLE':
        w = 0.0
    else:
        w = min(1.0, max(0.0, float(custom_weight)))
    return [target_con], {target_con.name: w}


class IslandSpaceSwitchSettings(PropertyGroup):
    mode: EnumProperty(
        name='切换范围',
        items=MODES,
        default='CURRENT',
    )
    target_influence: FloatProperty(
        name='目标权重',
        description='自定义切换到的子级约束影响权重（0 到 1），切换时保持世界变换',
        default=0.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
    )
    use_guard_key: BoolProperty(
        name='保护切换边界帧',
        description='在切换前一帧自动写入恒定保护关键帧（区间模式同时保护有后续动画的结束帧后一帧），避免既有动画漂移或手柄过冲',
        default=True,
    )
    auto_influence_compensate: BoolProperty(
        name='拖动影响滑条时自动保持世界空间',
        description='直接拖动或修改子级约束原生“影响（Influence）”滑条时，自动补偿局部变换以保持世界位置与旋转，并在有动画或开启自动插帧时写入保护关键帧',
        default=True,
    )
    frame_start: IntProperty(
        name='起始帧',
        description='帧范围保持动画模式的起始帧',
        default=1,
        min=-MAX_FRAME,
        max=MAX_FRAME,
    )
    frame_end: IntProperty(
        name='结束帧',
        description='帧范围保持动画模式的结束帧',
        default=250,
        min=-MAX_FRAME,
        max=MAX_FRAME,
    )


class CONSTRAINT_OT_island_childof_space_switch(Operator):
    """切换子级约束（Child Of）权重并保持当前世界位置与旋转，自动保护相邻动画关键帧"""
    bl_idname = 'constraint.island_childof_space_switch'
    bl_label = '子级约束空间切换'
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(name='操作', items=ACTIONS, default='TOGGLE')
    constraint_name: StringProperty(name='约束名称', default='')
    bone_name: StringProperty(name='骨骼名称', default='')
    owner_type: EnumProperty(name='所属类型', items=OWNER_TYPES, default='AUTO')
    mode: EnumProperty(name='切换范围', items=MODES, default='CURRENT')
    target_influence: FloatProperty(name='目标权重', default=0.0, min=0.0, max=1.0, subtype='FACTOR')
    use_guard_key: BoolProperty(name='保护切换边界帧', default=True)
    frame_start: IntProperty(name='起始帧', default=1, min=-MAX_FRAME, max=MAX_FRAME)
    frame_end: IntProperty(name='结束帧', default=250, min=-MAX_FRAME, max=MAX_FRAME)

    @classmethod
    def poll(cls, context):
        scene = getattr(context, 'scene', None)
        if scene is None or not scene.is_editable:
            return False
        ob = getattr(context, 'object', None) or getattr(context, 'active_object', None)
        return ob is not None and ob.is_editable

    def invoke(self, context, event):
        settings = getattr(context.scene, 'island_space_switch', None)
        if settings is not None:
            for name in ('mode', 'target_influence', 'use_guard_key', 'frame_start', 'frame_end'):
                if not self.properties.is_property_set(name):
                    setattr(self, name, getattr(settings, name))
        return self.execute(context)

    def execute(self, context):
        global _SUPPRESS_AUTO_CHILDOF_UPDATE
        prev_suppress = _SUPPRESS_AUTO_CHILDOF_UPDATE
        _SUPPRESS_AUTO_CHILDOF_UPDATE = True
        _DRAG_WORLD_CACHE.clear()
        try:
            return self._execute_impl(context)
        finally:
            _SUPPRESS_AUTO_CHILDOF_UPDATE = prev_suppress

    def _execute_impl(self, context):
        scene = context.scene
        orig_frame = scene.frame_current
        orig_subframe = scene.frame_subframe

        ob, pchan, constraints, target_con = resolve_owner_and_constraints(
            context, self.owner_type, self.constraint_name, self.bone_name
        )
        if ob is None or constraints is None:
            self.report({'WARNING'}, '请先选择包含子级约束的物体或姿态骨骼')
            return {'CANCELLED'}

        owner = pchan if pchan is not None else ob
        bone_name = pchan.name if pchan is not None else None

        try:
            switched_constraints, target_weights = _build_target_weights(
                constraints, target_con, self.action, self.target_influence
            )
            _validate_owner_animation_editable(ob, pchan, switched_constraints)
        except SpaceSwitchError as err:
            self.report({'WARNING'}, str(err))
            return {'CANCELLED'}

        # Full state backup for clean rollback if solving fails before any F-Curve write.
        initial_owner_state = _capture_local_state(owner)
        initial_con_states = [
            (c, float(c.influence), c.inverse_matrix.copy(), bool(c.set_inverse_pending))
            for c in switched_constraints
        ]

        def rollback():
            for c, inf, inv_mat, pending in initial_con_states:
                c.influence = inf
                c.inverse_matrix = inv_mat
                c.set_inverse_pending = pending
            _restore_local_state(owner, initial_owner_state)
            scene.frame_set(orig_frame, subframe=orig_subframe)
            context.view_layer.update()

        try:
            if self.mode == 'CURRENT':
                frame_t = float(orig_frame) + float(orig_subframe)
                if not -MAX_FRAME <= frame_t <= MAX_FRAME:
                    raise SpaceSwitchError('当前帧超出 Blender 支持的帧范围')

                # Capture live pose & world matrix at frame_t FIRST (preserves unkeyed pose edits!).
                world_t = _evaluated_world_matrix(context, ob, bone_name)
                for c in switched_constraints:
                    if abs(_evaluated_target_matrix(context, c).determinant()) <= 1e-10:
                        raise SpaceSwitchError(f'子级约束“{c.name}”的目标存在零缩放，矩阵不可逆')
                live_local_t = _capture_local_state(owner)

                frame_prev = frame_t - 1.0
                should_guard = bool(
                    self.use_guard_key
                    and frame_prev >= -MAX_FRAME
                    and (frame_t > float(scene.frame_start) or _has_any_keys_before(ob, switched_constraints, frame_t))
                )

                state_prev = None
                influences_prev = {}
                world_prev = None
                if should_guard:
                    _set_frame_exact(scene, frame_prev)
                    world_prev = _evaluated_world_matrix(context, ob, bone_name)
                    state_prev = _capture_local_state(owner)
                    influences_prev = {c.name: float(c.influence) for c in switched_constraints}
                    _set_frame_exact(scene, frame_t)

                # Solve at frame_t in memory before touching any F-Curve.
                _restore_local_state(owner, live_local_t)
                ref_euler = Euler(live_local_t['rotation_euler'])
                ref_quat = Quaternion(live_local_t['rotation_quaternion']).normalized()
                for c in switched_constraints:
                    c.influence = target_weights[c.name]
                solved_t, need_scale = _solve_owner_world_matrix(
                    context, ob, pchan, world_t, ref_euler=ref_euler, ref_quat=ref_quat
                )

                _write_current_mode_curves(
                    ob=ob,
                    pchan=pchan,
                    switched_constraints=switched_constraints,
                    target_weights=target_weights,
                    frame_t=frame_t,
                    solved_t=solved_t,
                    should_guard=should_guard,
                    frame_prev=frame_prev,
                    state_prev=state_prev,
                    influences_prev=influences_prev,
                    include_scale=need_scale,
                )

                # Verify evaluated animation result at guard frame and switch frame.
                if should_guard and world_prev is not None:
                    _set_frame_exact(scene, frame_prev)
                    check_prev = _evaluated_world_matrix(context, ob, bone_name)
                    if not _residual_matches(_world_residual(check_prev, world_prev, False), False):
                        raise SpaceSwitchError('前一帧保护验证未通过，可能受曲线修改器影响')
                _set_frame_exact(scene, frame_t)
                check_cur = _evaluated_world_matrix(context, ob, bone_name)
                if not _residual_matches(_world_residual(check_cur, world_t, need_scale), need_scale):
                    raise SpaceSwitchError('当前帧动画求值验证未通过，请检查曲线修改器或覆盖约束')

            else:
                f_start = int(min(self.frame_start, self.frame_end))
                f_end = int(max(self.frame_start, self.frame_end))
                if f_start < -MAX_FRAME or f_end > MAX_FRAME:
                    raise SpaceSwitchError('起止帧超出 Blender 支持的帧范围')

                guard_before = bool(
                    self.use_guard_key
                    and (f_start - 1) >= -MAX_FRAME
                    and (f_start > scene.frame_start or _has_any_keys_before(ob, switched_constraints, float(f_start)))
                )
                guard_after = bool(
                    self.use_guard_key
                    and (f_end + 1) <= MAX_FRAME
                    and _has_any_keys_after(ob, switched_constraints, float(f_end))
                )

                # Pass 1: Sample all pre-switch world & local states before modifying anything.
                state_before = None
                influences_before = {}
                if guard_before:
                    scene.frame_set(f_start - 1)
                    state_before = _capture_local_state(owner)
                    influences_before = {c.name: float(c.influence) for c in switched_constraints}

                desired_worlds = {}
                local_guesses = {}
                for frame in range(f_start, f_end + 1):
                    scene.frame_set(frame)
                    for c in switched_constraints:
                        if abs(_evaluated_target_matrix(context, c).determinant()) <= 1e-10:
                            raise SpaceSwitchError(f'第 {frame} 帧处子级约束“{c.name}”的目标存在零缩放，矩阵不可逆')
                    desired_worlds[frame] = _evaluated_world_matrix(context, ob, bone_name)
                    local_guesses[frame] = _capture_local_state(owner)

                state_after = None
                influences_after = {}
                if guard_after:
                    scene.frame_set(f_end + 1)
                    state_after = _capture_local_state(owner)
                    influences_after = {c.name: float(c.influence) for c in switched_constraints}

                # Pass 2: Solve all frames in [f_start, f_end] in memory first (atomic pre-check).
                solved_by_frame = {}
                any_scale = False
                prev_euler = None
                prev_quat = None
                for frame in range(f_start, f_end + 1):
                    scene.frame_set(frame)
                    _restore_local_state(owner, local_guesses[frame])
                    for c in switched_constraints:
                        c.influence = target_weights[c.name]
                    ref_eul = prev_euler if prev_euler is not None else Euler(local_guesses[frame]['rotation_euler'])
                    ref_q = prev_quat if prev_quat is not None else Quaternion(
                        local_guesses[frame]['rotation_quaternion']
                    ).normalized()
                    solved_state, frame_scale = _solve_owner_world_matrix(
                        context, ob, pchan, desired_worlds[frame], ref_euler=ref_eul, ref_quat=ref_q
                    )
                    solved_by_frame[frame] = solved_state
                    any_scale = any_scale or frame_scale
                    prev_euler = Euler(solved_state['rotation_euler'])
                    prev_quat = Quaternion(solved_state['rotation_quaternion']).normalized()

                # Pass 3: Commit all curves atomically.
                _write_range_mode_curves(
                    ob=ob,
                    pchan=pchan,
                    switched_constraints=switched_constraints,
                    target_weights=target_weights,
                    f_start=f_start,
                    f_end=f_end,
                    solved_by_frame=solved_by_frame,
                    guard_before=guard_before,
                    state_before=state_before,
                    influences_before=influences_before,
                    guard_after=guard_after,
                    state_after=state_after,
                    influences_after=influences_after,
                    include_scale=any_scale,
                )

        except SpaceSwitchError as err:
            rollback()
            self.report({'WARNING'}, str(err))
            return {'CANCELLED'}

        if ob.animation_data and ob.animation_data.action:
            ob.animation_data.action.update_tag()
        scene.frame_set(orig_frame, subframe=orig_subframe)
        context.view_layer.update()
        if context.window_manager is not None:
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
        return {'FINISHED'}


class ANIM_OT_island_space_switch_set_range(Operator):
    """将所选关键帧范围或场景范围填入子级约束空间切换的起止帧"""
    bl_idname = 'anim.island_space_switch_set_range'
    bl_label = '获取切换帧范围'
    bl_options = {'REGISTER', 'UNDO'}

    source: EnumProperty(name='来源', items=RANGE_SOURCES, default='SELECTED_KEYS')

    @classmethod
    def poll(cls, context):
        return context.scene is not None and context.scene.is_editable

    def execute(self, context):
        scene = context.scene
        settings = scene.island_space_switch
        if self.source == 'SCENE':
            if scene.use_preview_range:
                settings.frame_start = scene.frame_preview_start
                settings.frame_end = scene.frame_preview_end
            else:
                settings.frame_start = scene.frame_start
                settings.frame_end = scene.frame_end
            return {'FINISHED'}

        ob = getattr(context, 'object', None) or getattr(context, 'active_object', None)
        selected_times = []
        if ob is not None:
            for fc in _iter_assigned_fcurves(ob):
                for key in fc.keyframe_points:
                    if key.select_control_point:
                        selected_times.append(int(round(float(key.co.x))))
        if not selected_times:
            self.report({'WARNING'}, '当前活动物体没有选中的关键帧')
            return {'CANCELLED'}
        settings.frame_start = min(selected_times)
        settings.frame_end = max(selected_times)
        return {'FINISHED'}


def _match_constraint_on_object(ob, con_ptr: int):
    for c in ob.constraints:
        if c.as_pointer() == con_ptr:
            return ob, None, c
    if ob.pose is not None:
        for pchan in ob.pose.bones:
            for c in pchan.constraints:
                if c.as_pointer() == con_ptr:
                    return ob, pchan, c
    return None, None, None


def _find_owner_by_pointers(context, ob_ptr: int, con_ptr: int):
    scene = getattr(context, 'scene', None)
    if scene is not None:
        for ob in scene.objects:
            if ob.as_pointer() == ob_ptr:
                return _match_constraint_on_object(ob, con_ptr)
    for ob in bpy.data.objects:
        if ob.as_pointer() == ob_ptr:
            return _match_constraint_on_object(ob, con_ptr)
    return None, None, None


def _find_owner_by_constraint(context, con):
    if con is None:
        return None, None, None
    con_ptr = con.as_pointer()
    owner_id = getattr(con, 'id_data', None)
    if isinstance(owner_id, bpy.types.Object):
        return _match_constraint_on_object(owner_id, con_ptr)
    ob = getattr(context, 'object', None) or getattr(context, 'active_object', None)
    if ob is not None:
        found = _match_constraint_on_object(ob, con_ptr)
        if found[2] is not None:
            return found
    for cand in bpy.data.objects:
        found = _match_constraint_on_object(cand, con_ptr)
        if found[2] is not None:
            return found
    return None, None, None


def _owner_has_transform_fcurves(ob, pchan) -> bool:
    owner = pchan if pchan is not None else ob
    prefixes = {
        owner.path_from_id('location'),
        owner.path_from_id('rotation_euler'),
        owner.path_from_id('rotation_quaternion'),
        owner.path_from_id('rotation_axis_angle'),
        owner.path_from_id('scale'),
    }
    for fc in _iter_assigned_fcurves(ob):
        if fc.data_path in prefixes and len(fc.keyframe_points) > 0:
            return True
    return False


def _can_auto_switch_childof(ob_ptr: int, con_ptr: int, from_python: bool = False) -> bool:
    if _SUPPRESS_AUTO_CHILDOF_UPDATE:
        return False
    if from_python and not _FORCE_PYTHON_AUTO_CHILDOF_SWITCH:
        return False
    context = bpy.context
    scene = getattr(context, 'scene', None)
    if scene is None or not scene.is_editable:
        return False
    settings = getattr(scene, 'island_space_switch', None)
    if settings is not None and not settings.auto_influence_compensate:
        return False
    ob, pchan, con = _find_owner_by_pointers(context, ob_ptr, con_ptr)
    if ob is None or con is None:
        return False
    try:
        _validate_childof(con)
        _validate_owner_animation_editable(ob, pchan, [con])
    except SpaceSwitchError:
        return False
    return True


def _run_direct_childof_compensation(
    context,
    ob,
    pchan,
    con,
    prev_inf: float,
    requested_inf: float,
    had_inf_fcurve: bool,
) -> bool:
    scene = getattr(context, 'scene', None)
    if scene is None or not scene.is_editable:
        return False
    orig_frame = scene.frame_current
    orig_subframe = scene.frame_subframe
    frame_t = float(orig_frame) + float(orig_subframe)
    if not -MAX_FRAME <= frame_t <= MAX_FRAME:
        return False

    owner = pchan if pchan is not None else ob
    bone_name = pchan.name if pchan is not None else None
    requested_clamped = min(1.0, max(0.0, float(requested_inf)))

    initial_owner_state = _capture_local_state(owner)
    initial_inv_mat = con.inverse_matrix.copy()
    initial_pending = bool(con.set_inverse_pending)

    def rollback():
        con.influence = prev_inf
        con.inverse_matrix = initial_inv_mat
        con.set_inverse_pending = initial_pending
        _restore_local_state(owner, initial_owner_state)
        scene.frame_set(orig_frame, subframe=orig_subframe)
        context.view_layer.update()

    try:
        _validate_childof(con)
        _validate_owner_animation_editable(ob, pchan, [con])

        con.influence = prev_inf
        live_world_t = _evaluated_world_matrix(context, ob, bone_name)
        if abs(_evaluated_target_matrix(context, con).determinant()) <= 1e-10:
            raise SpaceSwitchError('目标存在零缩放')

        cache_key = (
            id(bpy.data),
            ob.as_pointer(),
            con.as_pointer(),
            ob.name,
            bone_name or '',
            con.name,
            int(round(frame_t * 1000.0)),
        )
        cached = _DRAG_WORLD_CACHE.get(cache_key)
        cache_hit = False
        if cached is not None:
            res_cache = _world_residual(live_world_t, cached['last_world'], True)
            if _residual_matches(res_cache, True, pos_tol=5e-6, rot_tol=5e-6, scale_tol=5e-6):
                cache_hit = True

        desired_world = cached['desired_world'].copy() if cache_hit else live_world_t.copy()

        should_key = bool(
            scene.tool_settings.use_keyframe_insert_auto
            or had_inf_fcurve
            or _owner_has_transform_fcurves(ob, pchan)
        )

        if not should_key:
            _restore_local_state(owner, initial_owner_state)
            ref_euler = Euler(initial_owner_state['rotation_euler'])
            ref_quat = Quaternion(initial_owner_state['rotation_quaternion']).normalized()
            con.influence = requested_clamped
            _solve_owner_world_matrix(
                context, ob, pchan, desired_world, ref_euler=ref_euler, ref_quat=ref_quat
            )
            solved_world = _evaluated_world_matrix(context, ob, bone_name)
            if len(_DRAG_WORLD_CACHE) > 16:
                _DRAG_WORLD_CACHE.clear()
            _DRAG_WORLD_CACHE[cache_key] = {
                'last_world': solved_world.copy(),
                'desired_world': desired_world.copy(),
                'has_prev_sample': False,
                'state_prev': None,
                'influences_prev': {},
                'world_prev': None,
            }
        else:
            settings = getattr(scene, 'island_space_switch', None)
            use_guard = True if settings is None else bool(settings.use_guard_key)
            frame_prev = frame_t - 1.0
            should_guard = bool(
                use_guard
                and frame_prev >= -MAX_FRAME
                and (frame_t > float(scene.frame_start) or _has_any_keys_before(ob, [con], frame_t))
            )

            state_prev = None
            influences_prev = {}
            world_prev = None
            if should_guard:
                if cache_hit and cached.get('has_prev_sample'):
                    state_prev = cached['state_prev']
                    influences_prev = dict(cached['influences_prev'])
                    world_prev = cached['world_prev']
                else:
                    _set_frame_exact(scene, frame_prev)
                    world_prev = _evaluated_world_matrix(context, ob, bone_name)
                    state_prev = _capture_local_state(owner)
                    influences_prev = {con.name: float(con.influence)}
                    _set_frame_exact(scene, frame_t)

            _restore_local_state(owner, initial_owner_state)
            ref_euler = Euler(initial_owner_state['rotation_euler'])
            ref_quat = Quaternion(initial_owner_state['rotation_quaternion']).normalized()
            con.influence = requested_clamped
            solved_t, need_scale = _solve_owner_world_matrix(
                context, ob, pchan, desired_world, ref_euler=ref_euler, ref_quat=ref_quat
            )

            _write_current_mode_curves(
                ob=ob,
                pchan=pchan,
                switched_constraints=[con],
                target_weights={con.name: requested_clamped},
                frame_t=frame_t,
                solved_t=solved_t,
                should_guard=should_guard,
                frame_prev=frame_prev,
                state_prev=state_prev,
                influences_prev=influences_prev,
                include_scale=need_scale,
            )

            if should_guard and world_prev is not None and not (cache_hit and cached.get('has_prev_sample')):
                _set_frame_exact(scene, frame_prev)
                check_prev = _evaluated_world_matrix(context, ob, bone_name)
                if not _residual_matches(_world_residual(check_prev, world_prev, False), False):
                    raise SpaceSwitchError('前一帧保护验证未通过')

            _set_frame_exact(scene, frame_t)
            check_cur = _evaluated_world_matrix(context, ob, bone_name)
            if not _residual_matches(_world_residual(check_cur, desired_world, need_scale), need_scale):
                raise SpaceSwitchError('当前帧求值验证未通过')

            if len(_DRAG_WORLD_CACHE) > 16:
                _DRAG_WORLD_CACHE.clear()
            _DRAG_WORLD_CACHE[cache_key] = {
                'last_world': check_cur.copy(),
                'desired_world': desired_world.copy(),
                'has_prev_sample': bool(should_guard),
                'state_prev': state_prev,
                'influences_prev': dict(influences_prev),
                'world_prev': world_prev.copy() if world_prev is not None else None,
            }

            if ob.animation_data and ob.animation_data.action:
                ob.animation_data.action.update_tag()
            scene.frame_set(orig_frame, subframe=orig_subframe)
            context.view_layer.update()

    except SpaceSwitchError:
        rollback()
        return False

    if context.window_manager is not None:
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
    return True


def _execute_auto_childof_switch(
    ob_ptr: int,
    con_ptr: int,
    prev_inf: float,
    requested_inf: float,
    had_inf_fcurve: bool,
) -> bool:
    global _SUPPRESS_AUTO_CHILDOF_UPDATE
    if _SUPPRESS_AUTO_CHILDOF_UPDATE:
        return False
    prev_suppress = _SUPPRESS_AUTO_CHILDOF_UPDATE
    _SUPPRESS_AUTO_CHILDOF_UPDATE = True
    try:
        context = bpy.context
        ob, pchan, con = _find_owner_by_pointers(context, ob_ptr, con_ptr)
        if ob is None or con is None:
            return False
        return _run_direct_childof_compensation(
            context, ob, pchan, con, prev_inf, requested_inf, had_inf_fcurve
        )
    finally:
        _SUPPRESS_AUTO_CHILDOF_UPDATE = prev_suppress


def disable_childof_keep_transform(context, con):
    """Exact Child Of implementation for CONSTRAINT_OT_disable_keep_transform ('X' button)."""
    global _SUPPRESS_AUTO_CHILDOF_UPDATE
    prev_suppress = _SUPPRESS_AUTO_CHILDOF_UPDATE
    _SUPPRESS_AUTO_CHILDOF_UPDATE = True
    _DRAG_WORLD_CACHE.clear()
    try:
        ob, pchan, resolved_con = _find_owner_by_constraint(context, con)
        if ob is not None and resolved_con is not None:
            prev_inf = float(resolved_con.influence)
            had_inf_fcurve = _find_fcurve(ob, resolved_con.path_from_id('influence'), 0) is not None
            if _run_direct_childof_compensation(
                context, ob, pchan, resolved_con, prev_inf, 0.0, had_inf_fcurve
            ):
                return {'FINISHED'}

        # Fallback if constraint has no target or cannot use space-switch solver.
        if ob is not None and pchan is not None:
            mat = ob.matrix_world @ pchan.matrix
            con.influence = 0.0
            pchan.matrix = ob.matrix_world.inverted() @ mat
        elif ob is not None:
            mat = ob.matrix_world.copy()
            con.influence = 0.0
            ob.matrix_world = mat
        else:
            con.influence = 0.0
        return {'FINISHED'}
    finally:
        _SUPPRESS_AUTO_CHILDOF_UPDATE = prev_suppress


classes = (
    IslandSpaceSwitchSettings,
    CONSTRAINT_OT_island_childof_space_switch,
    ANIM_OT_island_space_switch_set_range,
)


def register():
    bpy.types.Scene.island_space_switch = PointerProperty(type=IslandSpaceSwitchSettings)


def unregister():
    del bpy.types.Scene.island_space_switch

// -*- coding: utf-8 -*-
/** ③ 网页与 Excel 导出共用的"名字"解析（09-25 用户定版：两侧都得显示名字，不是 id、不是 room/field）。
 *  与 `workbench/tools/export_storyboard_xlsx.py` 的 speaker_name()/scene_cell() 保持同一条链，
 *  任一侧改了优先级，另一侧的测试就该报——这条规则不该有两个版本。 */
export function speakerName(
  id: string | undefined,
  boardActors: Record<string, { name?: string }> | undefined,
  archive: { id?: string; name?: string }[] | undefined,
): string {
  if (!id) return ''
  return boardActors?.[id]?.name || archive?.find((c) => c.id === id)?.name || id
}

/** 场景列：scene_ref → 场景档案名；没关联才回落到对话契约的预设地形名。 */
export function sceneLabel(
  shot: { scene_ref?: string; scene?: string },
  sceneNames: Record<string, string>,
): string {
  const ref = String(shot.scene_ref || '').replace(/^@scene:/, '').trim()
  if (ref) return sceneNames[ref] || ref
  if (shot.scene === 'field') return '外景'
  if (shot.scene === 'room') return '室内'
  return ''
}

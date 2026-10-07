export const characterStateElementId = (id:string) => 'character-state-' + encodeURIComponent(id)

/** 旧表演、音色入口重定向后，等待角色内容挂载再定位到对应栏目。 */
export function characterSectionTarget(state:unknown,hash:string) {
  if(typeof state==='string' && state) return characterStateElementId(state)
  const section=hash.replace(/^#/,'')
  return ['states','voices','performance'].includes(section)?section:''
}

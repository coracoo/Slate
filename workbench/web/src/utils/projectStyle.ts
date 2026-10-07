/** 同项目的风格更新依次读取最新值，只提交本轮改动的维度。 */
export function createProjectStyleWriter(
  read: (project:string) => Promise<Record<string,string>>,
  write: (project:string,style:Record<string,string>,patch:Record<string,string>) => Promise<unknown>,
) {
  const pending = new Map<string,Promise<Record<string,string>>>()
  return async (project:string, mutate:(style:Record<string,string>)=>void) => {
    const previous = pending.get(project) || Promise.resolve()
    const request = previous.catch(() => undefined).then(async () => {
      const before = await read(project)
      const style = {...before}
      mutate(style)
      const patch=Object.fromEntries(Object.entries(style).filter(([key,value])=>value!==before[key]))
      await write(project,style,patch)
      return style
    })
    pending.set(project,request)
    try { return await request }
    finally { if (pending.get(project) === request) pending.delete(project) }
  }
}

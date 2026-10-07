export interface BatchResult {id:string; ok:boolean; error?:string}

/** 固定本次选择，逐项执行；单项失败不吞掉，也不重复发送。 */
export async function runSelectedBatch(ids:string[], execute:(id:string)=>Promise<void>, report:(results:BatchResult[])=>void=()=>{}) {
  const targets=[...new Set(ids)]
  if(!targets.length) throw new Error('请先选择处理范围，空选择不会处理全部')
  const results:BatchResult[]=[]
  for(const id of targets) {
    try {await execute(id);results.push({id,ok:true})}
    catch(e) {results.push({id,ok:false,error:e instanceof Error?e.message:String(e)})}
    report([...results])
  }
  return results
}

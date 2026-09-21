import { watch, type Ref, type ComputedRef } from 'vue'
import { app } from '../stores/app'
import { boardSelection } from './productionUi'

/** 页面负责恢复，控件只负责记录用户选择，避免两处抢写默认集数。 */
export function useBoardSelection(value: Ref<string>, options: Ref<string[]> | ComputedRef<string[]>, page: string) {
  let previous = ''
  watch([()=>app.current, ()=>options.value.join('\u0000')], () => {
    const changed = previous !== app.current
    previous = app.current
    let stored = ''
    try { stored = localStorage.getItem(`wb.${app.current}.${page}.board`) || '' } catch {}
    value.value = boardSelection(options.value, changed ? '' : value.value, stored)
  }, {immediate:true, flush:'sync'})
}

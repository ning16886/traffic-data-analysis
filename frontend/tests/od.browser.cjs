// 使用独立测试依赖，避免把浏览器运行时加入应用打包。
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs')
const { chromium } = require(process.env.OD_PLAYWRIGHT_PATH || path.resolve(__dirname, '../../backend/data/od-dev/browser-check/node_modules/playwright'))

async function run() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  const errors = []
  let intentionalFailure = false
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error' && !intentionalFailure && !message.location().url.endsWith('/favicon.ico')) errors.push(message.text())
  })
  const count = (value) => page.waitForFunction((v) => document.querySelector('output[aria-label="出行总次数"]')?.textContent === v, value)
  const pairs = (value) => page.waitForFunction((v) => document.querySelector('output[aria-label="OD 对数量"]')?.textContent === v, value)
  const checks = []
  const captureUi = process.env.OD_CAPTURE_UI !== '0'
  try {
    await page.goto('http://127.0.0.1:5173/od')
    await count('2,274'); await pairs('1,985')
    await page.locator('.ant-pagination-next').click()
    await page.locator('.ant-pagination-item-active[title="2"]').waitFor()
    for (const [name, total, size] of [['早高峰', '471', '453'], ['晚高峰', '455', '424'], ['平峰', '1,134', '1,063'], ['接驳出行', '214', '201']]) {
      await page.getByRole('tab', { name, exact: true }).click()
      await count(total); await pairs(size)
      await page.locator('.ant-pagination-item-active[title="1"]').waitFor()
    }
    checks.push('initial data, all four filters, pagination and page reset')
    await page.getByRole('tab', { name: '全部出行', exact: true }).click()
    await count('2,274'); await pairs('1,985')
    await page.locator('.od-ranking canvas').waitFor()
    await page.getByText('查看排名数据', { exact: true }).click()
    assert.equal(await page.locator('.od-ranking-text li').count(), 20)
    assert.match(await page.locator('.od-ranking-text li').first().textContent(), /R00455.*R00452.*5 次/)
    await page.getByText('查看排名数据', { exact: true }).click()
    checks.push('Top20 canvas and accessible ranking match live API')
    const verifyDefinitions = async () => {
      const expected = (await (await page.request.get(`http://127.0.0.1:5173/api/od/matrix${await page.getByRole('tab', { name: '早高峰', exact: true }).getAttribute('aria-selected') === 'true' ? '?time_period=morning_peak' : ''}`)).json()).data
      const help = page.getByRole('button', { name: '统计口径', exact: true })
      await help.focus()
      await help.press('Enter')
      const definitions = page.getByRole('region', { name: '统计口径详情' })
      await definitions.waitFor()
      assert.equal(await help.getAttribute('aria-expanded'), 'true')
      assert.equal(await definitions.locator('[data-testid="od-matrix-coverage"]').textContent(), `${expected.displayed_trip_count.toLocaleString('en-US')} / ${expected.total_trip_count.toLocaleString('en-US')} 次出行`)
      await page.keyboard.press('Escape')
      await definitions.waitFor({ state: 'hidden' })
      assert.equal(await help.evaluate((el) => el === document.activeElement), true)
      assert.equal(await help.getAttribute('aria-expanded'), 'false')
    }
    await verifyDefinitions()
    await page.getByRole('tab', { name: '早高峰', exact: true }).click()
    await count('471'); await pairs('453')
    await verifyDefinitions()
    await page.getByRole('tab', { name: '全部出行', exact: true }).click()
    await count('2,274'); await pairs('1,985')
    assert.equal(await page.getByText('观察起终点之间的出行联系，比较不同时段的流向。', { exact: true }).count(), 0)
    checks.push('definitions keyboard, Escape/focus return, and coverage follows current filter')
    await page.locator('label.ant-radio-button-wrapper').filter({ hasText: '交叉矩阵' }).click()
    await page.locator('.od-matrix tbody tr').first().waitFor()
    assert.equal(await page.locator('.od-matrix tbody tr').count(), 20)
    assert.equal(await page.locator('.od-matrix thead th').count(), 21)
    await page.getByRole('heading', { name: '重点路段矩阵', exact: true }).waitFor()
    assert.equal(await page.locator('.od-matrix-meta > span').first().textContent(), '20 × 20')
    const maxCount = await page.locator('.od-matrix td').evaluateAll((cells) => Math.max(...cells.map((cell) => Number(cell.textContent))))
    assert.equal(await page.locator('.od-matrix-scale').getAttribute('aria-label'), `出行次数色标：0 至 ${maxCount} 次`)
    const matrixContrast = await page.locator('.od-matrix td').evaluateAll((cells) => {
      const darkest = cells.reduce((best, cell) => Number(cell.textContent) > Number(best.textContent) ? cell : best, cells[0])
      const ctx = new OffscreenCanvas(1, 1).getContext('2d')
      const rgba = (color) => {
        ctx.clearRect(0, 0, 1, 1); ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1)
        return Array.from(ctx.getImageData(0, 0, 1, 1).data, (value) => value / 255)
      }
      const blend = (fg, bg) => fg.slice(0, 3).map((value, i) => value * fg[3] + bg[i] * (1 - fg[3]))
      const luminance = (rgb) => rgb.map((v) => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
        .reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0)
      const style = getComputedStyle(darkest)
      const background = blend(rgba(style.backgroundColor), [1, 1, 1])
      const foreground = blend(rgba(style.color), background)
      const a = luminance(background), b = luminance(foreground)
      return (Math.max(a, b) + .05) / (Math.min(a, b) + .05)
    })
    assert.ok(matrixContrast >= 4.5, `Matrix text contrast ${matrixContrast} must meet 4.5:1`)
    checks.push('20 x 20 key-road matrix with actual dimension, numeric scale and readable contrast')
    let releaseMatrix
    let markMatrixRequested
    const matrixRequested = new Promise((resolve) => { markMatrixRequested = resolve })
    await context.route('**/api/od/matrix*', async (route) => {
      await new Promise((resolve) => { releaseMatrix = resolve; markMatrixRequested() })
      await route.continue()
    })
    await page.getByRole('button', { name: /刷\s*新/, exact: true }).click()
    await matrixRequested
    const help = page.getByRole('button', { name: '统计口径', exact: true })
    await help.click()
    const coverage = page.getByRole('region', { name: '统计口径详情' }).locator('[data-testid="od-matrix-coverage"]')
    await coverage.waitFor()
    assert.equal(await coverage.textContent(), '—')
    releaseMatrix()
    await count('2,274')
    assert.equal(await coverage.textContent(), '70 / 2,274 次出行')
    await page.keyboard.press('Escape')
    await context.unroute('**/api/od/matrix*')
    intentionalFailure = true
    await context.route('**/api/od/matrix*', (route) => route.fulfill({ status: 503, body: 'unavailable' }))
    await page.getByRole('button', { name: /刷\s*新/, exact: true }).click()
    await page.getByText('矩阵加载失败', { exact: true }).waitFor()
    await help.click()
    await coverage.waitFor()
    assert.equal(await coverage.textContent(), '—')
    await page.keyboard.press('Escape')
    await context.unroute('**/api/od/matrix*')
    await page.getByRole('button', { name: /重\s*试/, exact: true }).click()
    await count('2,274')
    await page.locator('.od-matrix tbody tr').first().waitFor()
    intentionalFailure = false
    checks.push('matrix loading/failure clears coverage; visible error and retry recover')
    await page.locator('label.ant-radio-button-wrapper').filter({ hasText: 'OD 明细' }).click()
    intentionalFailure = true
    await context.route('**/api/od/list*', (route) => route.fulfill({ status: 503, body: 'unavailable' }))
    await page.getByRole('button', { name: /刷\s*新/, exact: true }).click()
    await page.getByText('OD 明细加载失败', { exact: true }).waitFor()
    await context.unroute('**/api/od/list*')
    await page.getByRole('button', { name: /重\s*试/ }).click()
    await pairs('1,985')
    intentionalFailure = false
    checks.push('HTTP failure displayed and retry recovers')
    await context.route('**/api/od/list*', (route) => route.fulfill({ json: { code: 0, data: { items: [], total: 0, page: 1, page_size: 20, pages: 0 }, msg: '' } }))
    await page.getByRole('button', { name: /刷\s*新/, exact: true }).click()
    await page.getByText('暂无出行数据', { exact: true }).waitFor()
    await context.unroute('**/api/od/list*')
    await page.getByRole('button', { name: /刷\s*新/, exact: true }).click()
    await pairs('1,985')
    checks.push('empty list state')
    await context.route('**/api/od/top*', (route) => route.fulfill({ json: { code: 0, data: { items: [] }, msg: '' } }))
    await page.getByRole('button', { name: /刷\s*新/, exact: true }).click()
    await page.getByText('暂无排名', { exact: true }).waitFor()
    await context.unroute('**/api/od/top*')
    intentionalFailure = true
    await context.route('**/api/od/top*', (route) => route.fulfill({ status: 503, body: 'unavailable' }))
    await page.getByRole('button', { name: /刷\s*新/, exact: true }).click()
    await page.getByText('出行排名加载失败', { exact: true }).waitFor()
    await context.unroute('**/api/od/top*')
    await page.getByRole('button', { name: /重\s*试/ }).click()
    await page.locator('.od-ranking canvas').waitFor()
    intentionalFailure = false
    checks.push('ranking empty state, failure and retry')
    await context.route('**/api/od/list*', async (route) => {
      if (route.request().url().includes('morning_peak')) await new Promise((resolve) => setTimeout(resolve, 450))
      await route.continue()
    })
    const firstRequest = page.waitForRequest((request) => request.url().includes('/od/list?') && request.url().includes('morning_peak'))
    await page.getByRole('tab', { name: '早高峰', exact: true }).click()
    await firstRequest
    await page.getByRole('tab', { name: '晚高峰', exact: true }).click()
    await count('455'); await pairs('424')
    await page.waitForTimeout(650)
    await count('455'); await pairs('424')
    await context.unroute('**/api/od/list*')
    checks.push('late response cannot overwrite latest filter')
    await page.getByRole('tab', { name: '全部出行', exact: true }).click()
    await count('2,274'); await pairs('1,985')
    await page.waitForTimeout(350)
    const out = path.resolve(__dirname, '../../backend/data/od-dev')
    const chartOption = () => page.evaluate(async () => {
      const source = performance.getEntriesByType('resource').find((entry) => /\/echarts_core\.js/.test(entry.name))
      const { getInstanceByDom } = await import(source.name)
      return getInstanceByDom(document.querySelector('.od-ranking')).getOption()
    })
    const layouts = []
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 })
      await page.locator('label.ant-radio-button-wrapper').filter({ hasText: 'OD 明细' }).click()
      await page.locator('.od-ranking canvas').waitFor()
      await page.waitForTimeout(220)
      const metrics = await page.evaluate(() => {
        const content = document.querySelector('.layout-content')
        const chart = document.querySelector('.od-ranking')
        return {
          pageFits: document.documentElement.scrollWidth <= window.innerWidth && content.scrollWidth <= content.clientWidth,
          headingSize: getComputedStyle(document.querySelector('.od-page h1')).fontSize,
          detailSize: getComputedStyle(document.querySelector('.od-road small')).fontSize,
          font: getComputedStyle(document.querySelector('.od-page')).fontFamily,
          chartWidth: chart.clientWidth,
          buttonHeight: document.querySelector('.od-actions .ant-btn').getBoundingClientRect().height,
          cards: document.querySelectorAll('.od-page .ant-card').length,
        }
      })
      assert.equal(metrics.pageFits, true)
      assert.equal(metrics.headingSize, '24px')
      assert.equal(metrics.detailSize, '12px')
      assert.equal(metrics.cards, 2)
      if (width === 390) assert.ok(metrics.buttonHeight >= 44)
      const option = await chartOption()
      assert.equal(option.yAxis[0].axisLabel.fontSize, 12)
      assert.equal(option.yAxis[0].data[0].includes('\n'), metrics.chartWidth < 480)
      assert.ok(metrics.chartWidth - option.grid[0].left - option.grid[0].right > 100)
      layouts.push({ width, ...metrics, chartLeft: option.grid[0].left })
      await page.locator('.layout-content').evaluate((el) => { el.scrollTop = 0 })
      if (captureUi) await page.screenshot({ path: path.join(out, `od-ui-${width}.png`), fullPage: true })
      await page.locator('.layout-content').evaluate((el) => {
        el.scrollTop += document.querySelector('.od-ranking-card').getBoundingClientRect().top - el.getBoundingClientRect().top - 16
      })
      if (captureUi) await page.screenshot({ path: path.join(out, `od-ui-ranking-${width}.png`) })
      await page.locator('label.ant-radio-button-wrapper').filter({ hasText: '交叉矩阵' }).click()
      await page.locator('.od-matrix tbody tr').first().waitFor()
      assert.equal(await page.locator('.od-matrix-scroll').evaluate((el) => el.scrollWidth > el.clientWidth), true)
      assert.equal(await page.locator('.od-matrix thead th').first().evaluate((el) => getComputedStyle(el).position), 'sticky')
      assert.equal(await page.locator('.od-matrix th span').first().evaluate((el) => getComputedStyle(el).fontSize), '12px')
      assert.equal(await page.locator('.layout-content').evaluate((el) => el.scrollWidth <= el.clientWidth), true)
      await page.locator('.od-matrix-scroll').focus()
      await page.keyboard.press('ArrowRight')
      await page.waitForFunction(() => document.querySelector('.od-matrix-scroll').scrollLeft > 0)
      await page.locator('.od-matrix-scroll').evaluate((el) => { el.scrollLeft = 0 })
      await page.locator('.layout-content').evaluate((el) => {
        el.scrollTop += document.querySelector('.od-view-toolbar').getBoundingClientRect().top - el.getBoundingClientRect().top - 16
      })
      if (captureUi) await page.screenshot({ path: path.join(out, `od-ui-matrix-${width}.png`) })
    }
    fs.writeFileSync(path.join(out, 'od-ui-layouts.json'), JSON.stringify(layouts, null, 2))
    checks.push('1440/768/390 layout, readable type, two cards, chart labels and keyboard matrix scroll')
    await page.locator('.ant-tabs-nav-more').focus()
    await page.keyboard.press('Enter')
    await page.getByRole('option', { name: '接驳出行', exact: true }).click()
    await count('214'); await pairs('201')
    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.getByRole('tab', { name: '全部出行', exact: true }).click()
    await count('2,274'); await pairs('1,985')
    await page.locator('.od-ranking canvas').waitFor()
    checks.push('narrow overflow category menu is keyboard accessible and selects original transit')
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.waitForTimeout(100)
    assert.equal((await chartOption()).animation, false)
    assert.equal(await page.locator('.od-actions .ant-btn').first().evaluate((el) => getComputedStyle(el).transitionDuration), '0s')
    await context.route('**/api/od/list*', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 700))
      await route.continue()
    })
    await page.locator('label.ant-radio-button-wrapper').filter({ hasText: 'OD 明细' }).click()
    await page.getByRole('button', { name: /刷\s*新/, exact: true }).click()
    await page.locator('.od-loading .ant-skeleton').first().waitFor()
    assert.equal(await page.locator('.od-loading .ant-skeleton-title').first().evaluate((el) => getComputedStyle(el, '::after').animationName), 'none')
    await pairs('1,985'); await context.unroute('**/api/od/list*')
    await page.locator('.od-ranking canvas').waitFor()
    assert.equal((await chartOption()).animation, false)
    await page.emulateMedia({ reducedMotion: 'no-preference' })
    await page.waitForTimeout(100)
    assert.equal((await chartOption()).animation, true)
    checks.push('reduced motion stops chart, skeleton and control transitions, including live preference changes')
    await page.setViewportSize({ width: 1440, height: 1000 })
    let releaseComputation
    let markComputationRequested
    const computationRequested = new Promise((resolve) => { markComputationRequested = resolve })
    await context.route('**/api/od/recompute', async (route) => {
      await new Promise((resolve) => { releaseComputation = resolve; markComputationRequested() })
      await route.continue()
    })
    await page.getByRole('button', { name: '重算 OD', exact: true }).click()
    await computationRequested
    let computedResponse
    try {
      // Ant Design 的 loading 图标会加入按钮可访问名，忙碌时使用稳定的操作区定位。
      assert.equal(await page.locator('.od-actions .ant-btn-primary').isDisabled(), true)
      assert.match(await page.locator('.od-source-row .ant-select').getAttribute('class'), /ant-select-disabled/)
      computedResponse = page.waitForResponse((response) => response.url().endsWith('/api/od/recompute') && response.request().method() === 'POST', { timeout: 180000 })
    } finally {
      releaseComputation()
    }
    const computation = (await (await computedResponse).json()).data
    await context.unroute('**/api/od/recompute')
    assert.ok(computation.available)
    await page.waitForFunction(() => document.querySelector('.od-source-row .ant-select-selection-item')?.textContent === '轨迹重算结果')
    await count(computation.stats.trip_count.toLocaleString('en-US'))
    await page.getByRole('status').filter({ hasText: '重算完成' }).waitFor()
    await page.locator('time.od-updated').waitFor()
    const updated = page.locator('time.od-updated')
    assert.match(await updated.textContent(), /^更新于 \d{2}-\d{2} \d{2}:\d{2}$/)
    assert.equal(await updated.getAttribute('datetime'), computation.completed_at)
    assert.match(await updated.getAttribute('title'), /北京时间/)
    assert.match(await updated.getAttribute('aria-label'), /\d{4}.*北京时间/)
    await page.getByRole('status').filter({ hasText: '重算完成' }).waitFor({ state: 'hidden', timeout: 6500 })
    assert.equal(await page.locator('.od-notice.ant-alert-success').count(), 0)
    await page.locator('.od-ranking canvas').waitFor()
    if (captureUi) await page.screenshot({ path: path.join(out, 'od-computed.png'), fullPage: true })
    await page.getByRole('tab', { name: '接驳出行', exact: true }).click()
    await count('0'); await pairs('0')
    await page.getByText('重算结果不含接驳分类', { exact: true }).waitFor()
    await page.getByText('暂无排名', { exact: true }).waitFor()
    const selectOriginal = async () => {
      await page.locator('.od-source-row .ant-select').click()
      await page.locator('.ant-select-item-option').filter({ hasText: '原始模拟结果' }).click()
      await page.getByRole('tab', { name: '全部出行', exact: true }).click()
      await count('2,274'); await pairs('1,985')
    }
    await selectOriginal()
    await context.route('**/api/od/recompute', (route) => route.fulfill({ json: { code: 5001, data: null, msg: '模拟失败，原有结果保留' } }))
    await page.getByRole('button', { name: '重算 OD', exact: true }).click()
    await page.getByText('重算失败', { exact: true }).waitFor()
    await count('2,274')
    await context.unroute('**/api/od/recompute')
    const retryResponse = page.waitForResponse((response) => response.url().endsWith('/api/od/recompute') && response.request().method() === 'POST', { timeout: 180000 })
    await page.getByRole('button', { name: '重试重算', exact: true }).click()
    assert.equal((await (await retryResponse).json()).code, 0)
    await page.waitForFunction(() => document.querySelector('.od-source-row .ant-select-selection-item')?.textContent === '轨迹重算结果')
    await count(computation.stats.trip_count.toLocaleString('en-US'))
    await selectOriginal()
    checks.push('real recomputation, computed views, zero transit, original preservation and failed recompute retry')
    await page.locator('a[href="/trajectory"]').click()
    await page.waitForURL('**/trajectory')
    assert.equal(await page.locator('.od-ranking').count(), 0)
    assert.equal(await page.evaluate(() => document.body.classList.contains('od-route')), false)
    await page.locator('a[href="/od"]').click()
    await count('2,274'); await pairs('1,985')
    await page.locator('.od-ranking canvas').waitFor()
    checks.push('leaving and returning cleans up chart and scoped layout')
    assert.deepEqual(errors, [])
    const report = { checks, errors, browser: browser.version(), verifiedAt: new Date().toISOString(), layouts, matrixContrast }
    fs.writeFileSync(path.join(out, 'od-browser-report.json'), JSON.stringify(report, null, 2))
    console.log(JSON.stringify(report, null, 2))
  } finally {
    await browser.close()
  }
}

run().catch((error) => { console.error(error); process.exitCode = 1 })

// Source selectors follow the complete unit recommended for each safe text fragment.
export function learningUnit(page, surface) {
  return page.locator(`.japanese-text .learning-fragment[data-default-surface=${JSON.stringify(surface)}]`).first()
}

export async function selectLearningUnit(page, surface, kind) {
  const preferred = learningUnit(page, surface)
  if (await preferred.count()) {
    await preferred.click()
  } else {
    // Structures which overlap another default remain available in the nearby inspector.
    const sentence = page.locator('.japanese-text .sentence').filter({ hasText: surface }).first()
    await sentence.locator('.learning-fragment[role="button"]').first().click()
    const structures = page.locator('.grammar-structure-panel')
    if (!await structures.evaluate((node) => node.open)) await structures.locator('summary').click()
    await structures.getByRole('button').filter({ has: page.locator('strong').getByText(surface, { exact: true }) }).first().click()
  }
  await page.waitForFunction(({ surface, kind }) => {
    const inspector = document.querySelector('.learning-inspector')
    return inspector?.querySelector('.inspector-surface')?.textContent === surface
      && (!kind || inspector.querySelector('.inspector-heading h3')?.textContent === kind)
  }, { surface, kind })
}

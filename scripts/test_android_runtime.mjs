import { connect } from './android_cdp.mjs'
import { readFile, writeFile } from 'node:fs/promises'
import assert from 'node:assert/strict'
const page = await connect()
const report = { tokenizer: 0, epub: 0, assets: 0 }
try {
  const corpus = JSON.parse(await readFile('build/token-corpus.json', 'utf8'))
  for (let i = 0; i < corpus.length; i++) {
    const fixture = corpus[i]
    const result = await page.evaluate(`fetch('/api/chapters/segment',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify({chapter_id:`fixture-${i}`,text:fixture.text})})}).then(async r=>({status:r.status,body:await r.json()}))`)
    assert.equal(result.status, 200, JSON.stringify(result))
    const actual = result.body.tokens.map(t => Object.fromEntries(Object.entries(t).filter(([k]) => ['surface','lemma','reading','part_of_speech','start','end'].includes(k))))
    assert.deepEqual(actual, fixture.expected, fixture.text)
    report.tokenizer++
  }
  for (let i = 0; i < 10; i++) {
    const bytes = (await readFile(`build/fixture-${i}.epub`)).toString('base64')
    const result = await page.evaluate(`(async()=>{const bytes=Uint8Array.from(atob(${JSON.stringify(bytes)}),c=>c.charCodeAt(0));const form=new FormData();form.append('file',new File([bytes],'验收${i}.epub',{type:'application/epub+zip'}));const r=await fetch('/api/import/epub',{method:'POST',body:form});return {status:r.status,body:await r.json()}})()`)
    assert.equal(result.status, 200, JSON.stringify(result))
    assert.equal(result.body.chapters.length, 2)
    assert.ok(result.body.chapters[0].text.includes('姿を目にする'))
    const bookId = `android-epub-fixture-${i}`
    const patch = { upserts: {
      books: [{id:bookId,title:result.body.title,author:'验收',showImages:true}],
      chapters: result.body.chapters.map(c=>({...c,id:`${bookId}:${c.id}`,bookId,originalHtmlUrl:c.original_html_url,status:'pending'})),
    }}
    assert.equal(await page.evaluate(`fetch('/api/library',{method:'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify(${JSON.stringify(patch)})}).then(r=>r.status)`), 200)
    const urls = result.body.chapters.flatMap(c => c.blocks.filter(b=>b.asset_url).map(b=>b.asset_url))
    assert.ok(urls.length, 'EPUB image blocks must survive import')
    for (const url of urls) {
      assert.equal(await page.evaluate(`fetch(${JSON.stringify(url)}).then(r=>r.status)`), 200)
      report.assets++
    }
    for (const chapter of result.body.chapters) {
      assert.equal(await page.evaluate(`fetch(${JSON.stringify(chapter.original_html_url)}).then(r=>r.status)`), 200)
    }
    report.epub++
  }
  await writeFile('build/android-runtime-test.json', JSON.stringify(report, null, 2))
  console.log(report)
} finally { page.close() }

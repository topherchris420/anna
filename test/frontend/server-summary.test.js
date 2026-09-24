/* Execute the server page's progressive enhancement without external packages. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const template = fs.readFileSync(path.join(__dirname, '../../allthethings/engine_web/templates/engine/search.html'), 'utf8');
const script = template.slice(template.lastIndexOf('<script>') + 8, template.lastIndexOf('</script>'));

async function render(payload) {
  const nodes = [];
  function element(tag) {
    const node = { tag, textContent: '', children: [], style: {},
      classList: { remove() {} }, appendChild(child) { this.children.push(child); } };
    // Any attempt to parse source/model text as HTML fails the regression.
    Object.defineProperty(node, 'innerHTML', { set() { throw new Error('Unsafe HTML sink'); } });
    nodes.push(node);
    return node;
  }
  const head = element('h3'), answer = element('div'), box = element('div');
  box.getAttribute = () => 'DMA';
  box.querySelector = selector => selector === 'h3' ? head : answer;
  vm.runInNewContext(script, {
    document: { getElementById: () => box, createElement: element },
    fetch: async () => ({ json: async () => payload }), URL,
  });
  await new Promise(resolve => setImmediate(resolve));
  return { nodes, head, answer, box };
}

test('server report preserves hostile source/model text without parsing HTML', async () => {
  const quote = '<svg onload=alert(1)>DMA evidence.';
  const result = await render({
    grounding: 'source-extract', answer: '<img onerror=alert(1)> DMA [1]',
    citations: [{ n: 1, title: '<script>bad</script>', source: '<img>', url: 'javascript:alert(1)',
      excerpts: [{ quote, field: 'abstract', start: 0, end: quote.length, offset_unit: 'unicode-code-points' }] }],
  });
  assert.notEqual(result.box.style.display, 'none');
  assert.equal(result.answer.textContent, '<img onerror=alert(1)> DMA [1]');
  assert.equal(result.nodes.find(n => n.tag === 'blockquote').textContent, quote);
  assert.equal(result.nodes.filter(n => ['script', 'img', 'svg', 'a'].includes(n.tag)).length, 0);
});

test('server report links safe original sources and labels model prose', async () => {
  const result = await render({grounding: 'references-only', answer: 'DMA claim. [1]',
    citations: [{n: 1, title: 'Manual', source: 'vendor', url: 'https://example.org/manual', excerpts: []}]});
  const link = result.nodes.find(n => n.tag === 'a');
  assert.equal(link.href, 'https://example.org/manual');
  assert.equal(link.rel, 'noopener noreferrer');
  assert.match(result.head.textContent, /review sources/);
});

test('server report never displays uncited model prose', async () => {
  const result = await render({answer: 'A fabricated claim', citations: []});
  assert.doesNotMatch(result.answer.textContent, /fabricated/);
  assert.match(result.head.textContent, /No matching evidence/);
});

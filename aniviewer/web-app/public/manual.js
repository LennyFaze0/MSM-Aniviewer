const input = document.querySelector('#manualSearch');
const topics = [...document.querySelectorAll('[data-topic]')];
const links = [...document.querySelectorAll('nav a')];
function filterTopics() {
  const words = input.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  let count = 0;
  for (const topic of topics) {
    const text = topic.textContent.toLowerCase();
    topic.hidden = !words.every((word) => text.includes(word));
    if (!topic.hidden) count++;
    const link = links.find((entry) => entry.hash === `#${topic.id}`);
    if (link) link.hidden = topic.hidden;
  }
  document.querySelector('#searchStatus').textContent = words.length ? `${count} matching section${count === 1 ? '' : 's'}` : '';
  document.querySelector('#noResults').hidden = count !== 0;
}
input.addEventListener('input', filterTopics);
function revealHash() {
  const topic = topics.find((entry) => `#${entry.id}` === location.hash);
  if (topic?.hidden) { input.value = ''; filterTopics(); topic.scrollIntoView(); }
}
window.addEventListener('hashchange', revealHash);
const printButton = document.querySelector('#printManual');
printButton.hidden = false;
printButton.addEventListener('click', () => window.print());

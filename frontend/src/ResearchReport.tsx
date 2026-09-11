/** Deliberately plain text. No HTML/Markdown renderer, link previews or remote images. */
export function ResearchReport({report}:{report:{report:string;citations:{citationID:string;URL:string}[]}}) {
 return <section aria-label="Research report"><pre>{report.report}</pre><ul>{report.citations.map(c=><li key={c.citationID}>{c.citationID}: {c.URL}</li>)}</ul></section>;
}

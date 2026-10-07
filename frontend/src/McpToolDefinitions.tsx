import React, {useEffect, useRef, useState} from 'react';
import {Alert, Box, ExpandableSection, FileUpload, FormField, SpaceBetween, Table, Textarea} from '@cloudscape-design/components';

export type McpTool = {name: string; description: string; inputSchema: {[key: string]: unknown}};
type Definitions = {tools?: McpTool[]; error?: string; source?: 'snowflake'};
const maximumBytes = 180_000;
const object = (value: unknown): value is {[key: string]: unknown} =>
  !!value && typeof value === 'object' && !Array.isArray(value);

export function parseToolDefinitions(text: string): Definitions {
  if (!text.trim()) return {error: 'Load the server’s tool definitions to continue.'};
  if (new TextEncoder().encode(text).length > maximumBytes) return {error: 'Tool definitions must be at most 180 KB.'};
  let document: unknown;
  try {document = JSON.parse(text);} catch {return {error: 'This is not valid JSON. Paste the server definition or upload a JSON file.'};}
  const payload = object(document) && object(document.result) ? document.result : document;
  if (!object(payload) || !Array.isArray(payload.tools) || !payload.tools.length || payload.tools.length > 100) {
    return {error: 'Use a server definition or tools/list response containing a tools array with 1–100 tools.'};
  }
  const names = new Set<string>(), tools: McpTool[] = [];
  let converted = false;
  for (const [index, tool] of payload.tools.entries()) {
    if (!object(tool) || typeof tool.name !== 'string' || !/^[A-Za-z0-9_.-]{1,100}$/.test(tool.name)) {
      return {error: `Tool ${index + 1} needs a name of 1–100 letters, numbers, underscores, dots or hyphens.`};
    }
    if (names.has(tool.name)) return {error: 'Each tool must have a unique name.'};
    names.add(tool.name);
    let inputSchema = tool.inputSchema;
    if (inputSchema === undefined && typeof tool.type === 'string') {
      if (payload.version !== 1) return {error: 'Snowflake server specification version must be 1.'};
      if (tool.type !== 'SYSTEM_EXECUTE_SQL') {
        return {error: `Snowflake tool "${tool.name}" uses type "${tool.type}", which Studio cannot convert yet.`};
      }
      // Snowflake owns config (including warehouse and read_only). Only the
      // built-in tool's call arguments belong in the Gateway input schema.
      inputSchema = {type: 'object', properties: {
        sql: {description: 'Single SQL query to execute.', type: 'string'},
      }};
      converted = true;
    }
    if (!object(inputSchema) || inputSchema.type !== 'object') {
      return {error: `Tool ${index + 1} needs an inputSchema with type "object".`};
    }
    if (tool.description !== undefined && typeof tool.description !== 'string') {
      return {error: `Tool ${index + 1} must use text for its description.`};
    }
    const pending: unknown[] = [inputSchema];
    while (pending.length) {
      const value = pending.pop();
      if (Array.isArray(value)) pending.push(...value);
      else if (object(value)) {
        if ('$ref' in value && (typeof value.$ref !== 'string' || !value.$ref.startsWith('#'))) {
          return {error: 'Tool schemas cannot reference external documents.'};
        }
        pending.push(...Object.values(value));
      }
    }
    tools.push({name: tool.name, description: (tool.description || '') as string, inputSchema});
  }
  return {tools, ...(converted ? {source: 'snowflake' as const} : {})};
}

export default function McpToolDefinitions({value, onChange, validation, packageName}: {
  value: string; onChange: (value: string) => void; validation: Definitions; packageName?: string;
}) {
  const [file, setFile] = useState<File[]>([]), [fileError, setFileError] = useState('');
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  useEffect(() => () => {generation.current++;}, []);
  async function choose(files: File[]) {
    const current = ++generation.current;
    setFile(files); setFileError(''); onChange('');
    const selected = files[0];
    if (!selected) {setLoading(false); return;}
    if (!selected.size || selected.size > maximumBytes) {
      setFileError('Choose a nonempty JSON file of at most 180 KB.'); setLoading(false); return;
    }
    setLoading(true);
    try {
      const text = await selected.text();
      if (current === generation.current) onChange(text);
    } catch {if (current === generation.current) setFileError('The file could not be read. Choose it again.');}
    finally {if (current === generation.current) setLoading(false);}
  }
  return <SpaceBetween size="m">
    {packageName ? <Alert type="success" header="Tools loaded from your package">
      Studio loaded the definitions from {packageName}. No JSON file is needed.
    </Alert> : <>
      <Box>User sign-in connections need the server’s tool definitions before publication.
        Paste or upload the server’s JSON. For Snowflake, copy the server_spec cell returned by DESCRIBE MCP SERVER.
        Studio prepares supported tool inputs automatically. Users sign in when an agent calls a tool.</Box>
      <FormField label="Tool definitions file" description="Snowflake server_spec JSON, MCP tool definitions, or a tools/list response. Maximum 180 KB."
        errorText={fileError || (file.length && validation.error ? validation.error : undefined)}>
        <FileUpload accept=".json,application/json" value={file} onChange={({detail}) => void choose(detail.value)}
          i18nStrings={{uploadButtonText: () => 'Choose tool definitions file', dropzoneText: () => 'Drop a JSON file',
            removeFileAriaLabel: index => `Remove file ${index + 1}`, errorIconAriaLabel: 'Error',
            limitShowFewer: 'Show fewer files', limitShowMore: 'Show more files'}}/>
      </FormField>
      <ExpandableSection headerText="Paste tool definitions instead">
        <FormField label="MCP tool schema JSON" description="Paste Snowflake’s server_spec value or an MCP tools/list response without editing it."
          errorText={value.trim() && validation.error ? validation.error : undefined}>
          <Textarea ariaLabel="MCP tool schema JSON" rows={8} value={value} onChange={({detail}) => {
            generation.current++; setLoading(false); setFile([]); setFileError(''); onChange(detail.value);
          }}/>
        </FormField>
      </ExpandableSection>
      {!validation.tools && !fileError && !loading && <Box color="text-status-info">{validation.error}</Box>}
      {loading && <Box>Reading tool definitions…</Box>}
    </>}
    {validation.source === 'snowflake' && <Alert type="success" header="Snowflake definition ready">
      Studio prepared the SQL tool inputs. Warehouse and read-only settings remain on the Snowflake server.
    </Alert>}
    {validation.tools && <Table items={validation.tools} wrapLines
      header={<Box variant="h3">{validation.tools.length} tool {validation.tools.length === 1 ? 'definition' : 'definitions'} loaded</Box>}
      columnDefinitions={[{id: 'name', header: 'Tool', width: 160, minWidth: 100, cell: tool => tool.name},
        {id: 'description', header: 'Description', cell: tool => tool.description}]}/>}
  </SpaceBetween>;
}

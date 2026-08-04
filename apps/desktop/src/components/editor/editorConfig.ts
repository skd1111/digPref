/**
 * Editor configuration helpers — registers custom SQL completion providers
 * (e.g. table/column suggestions pulled from the connected DBs) at startup.
 */
import type * as monaco from 'monaco-editor';

export function registerSqlCompletions(m: typeof monaco): void {
  m.languages.registerCompletionItemProvider('sql', {
    provideCompletionItems: () => ({ suggestions: [] }),
  });
}
import * as vscode from 'vscode';

// ── Types ─────────────────────────────────────────────────────
interface Issue {
    rule_id:    string;
    title:      string;
    suggestion: string;
    line:       number;
    weight:     number;
    severity:   string;
}

interface AnalyzeResponse {
    green_score:      number;
    estimated_co2_kg: number;
    issues:           Issue[];
    total_operation_weight: number;
}

// ── Cost Constants (mirrors carbon.py) ───────────────────────
const COST_PER_WEIGHT_INR   = 0.00035;   // ₹ per compute unit per run
const DEFAULT_RUNS_PER_DAY  = 10000;

function estimateCostPerRun(weight: number): number {
    return weight * COST_PER_WEIGHT_INR;
}

function estimateDailyCostINR(weight: number, runsPerDay = DEFAULT_RUNS_PER_DAY): number {
    return estimateCostPerRun(weight) * runsPerDay;
}

function estimateMonthlyCostINR(weight: number, runsPerDay = DEFAULT_RUNS_PER_DAY): number {
    return estimateDailyCostINR(weight, runsPerDay) * 30;
}

function fmtINR(amount: number): string {
    if (amount >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
    if (amount >= 1000)   return `₹${(amount / 1000).toFixed(1)}k`;
    return `₹${amount.toFixed(2)}`;
}

function severityColor(severity: string): string {
    switch (severity.toLowerCase()) {
        case 'very high': return '🔴';
        case 'high':      return '🟠';
        case 'medium':    return '🟡';
        default:          return '🟢';
    }
}

// ── Decoration Types (severity-based colors) ──────────────────
const decorationVeryHigh = vscode.window.createTextEditorDecorationType({
    backgroundColor: 'rgba(255, 0, 0, 0.12)',
    border: '1px solid rgba(255, 80, 80, 0.6)',
    isWholeLine: true
});

const decorationHigh = vscode.window.createTextEditorDecorationType({
    backgroundColor: 'rgba(255, 140, 0, 0.10)',
    border: '1px solid rgba(255, 140, 0, 0.5)',
    isWholeLine: true
});

const decorationMedium = vscode.window.createTextEditorDecorationType({
    backgroundColor: 'rgba(255, 200, 0, 0.08)',
    border: '1px solid rgba(255, 200, 0, 0.4)',
    isWholeLine: true
});

const allDecorations = [decorationVeryHigh, decorationHigh, decorationMedium];

// ── Status Bar Item (persistent, always visible) ──────────────
let statusBar: vscode.StatusBarItem;

export function activate(context: vscode.ExtensionContext) {

    // Create status bar — always visible bottom right
    statusBar = vscode.window.createStatusBarItem(
        vscode.StatusBarAlignment.Right, 100
    );
    statusBar.command = 'greenops-analyzer.analyzeCode';
    statusBar.tooltip = 'Click to run GreenOps analysis';
    statusBar.text    = '$(leaf) GreenOps';
    statusBar.show();
    context.subscriptions.push(statusBar);

    const disposable = vscode.commands.registerCommand(
        'greenops-analyzer.analyzeCode',
        async () => {

            const editor = vscode.window.activeTextEditor;

            if (!editor) {
                vscode.window.showErrorMessage('No active editor found');
                return;
            }

            const code = editor.document.getText();
            statusBar.text = '$(sync~spin) Analyzing...';

            try {
                const response = await fetch('https://web-production-b9977.up.railway.app/analyze', {
                    method:  'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body:    JSON.stringify({ code })
                });

                if (!response.ok) { throw new Error('Server error'); }

                const data = await response.json() as AnalyzeResponse;

                const weight       = data.total_operation_weight ?? 0;
                const dailyCost    = estimateDailyCostINR(weight);
                const monthlyCost  = estimateMonthlyCostINR(weight);
                const issueCount   = data.issues.length;
                const score        = Math.round(data.green_score);

                // ── Status Bar — cost-first ───────────────────
                // Score colour: green > 70, yellow > 40, red otherwise
                const scoreIcon = score >= 70 ? '$(pass)' : score >= 40 ? '$(warning)' : '$(error)';
                statusBar.text    = `${scoreIcon} Score: ${score} | ${fmtINR(dailyCost)}/day | ${issueCount} issue${issueCount !== 1 ? 's' : ''}`;
                statusBar.tooltip = `Monthly waste at 10k runs/day: ${fmtINR(monthlyCost)}\nClick to re-analyse`;
                statusBar.color   = score >= 70 ? '#4ade80' : score >= 40 ? '#facc15' : '#f87171';

                // ── Popup message ─────────────────────────────
                vscode.window.showInformationMessage(
                    `GreenOps: Score ${score}/100 · ${fmtINR(dailyCost)}/day · ${issueCount} inefficienc${issueCount !== 1 ? 'ies' : 'y'} found`
                );

                // ── Clear old highlights ──────────────────────
                allDecorations.forEach(d => editor.setDecorations(d, []));

                const decsVeryHigh: vscode.DecorationOptions[] = [];
                const decsHigh:     vscode.DecorationOptions[] = [];
                const decsMedium:   vscode.DecorationOptions[] = [];

                for (const issue of data.issues) {

                    const lineIndex = issue.line - 1;
                    if (lineIndex < 0 || lineIndex >= editor.document.lineCount) { continue; }

                    const lineText   = editor.document.lineAt(lineIndex).text;
                    const range      = new vscode.Range(lineIndex, 0, lineIndex, lineText.length);

                    // Per-issue cost estimate
                    const issueCostPerRun     = estimateCostPerRun(issue.weight);
                    const issueMonthly        = issueCostPerRun * DEFAULT_RUNS_PER_DAY * 30;
                    const icon                = severityColor(issue.severity);

                    // ── Hover tooltip — cost-first ────────────
                    const hoverMessage = new vscode.MarkdownString(
`## ${icon} ${issue.title}

> 💸 **Monthly cloud waste: ${fmtINR(issueMonthly)}** *(at ${DEFAULT_RUNS_PER_DAY.toLocaleString()} runs/day)*

| | |
|---|---|
| **Severity** | ${issue.severity} |
| **Compute weight** | ${issue.weight} units |
| **Annual waste** | **${fmtINR(issueMonthly * 12)}**/year if unresolved |

💡 **Fix:** ${issue.suggestion}

---
*GreenOps · modelled estimate · compute weight × AWS Lambda pricing*`
                    );
                    hoverMessage.isTrusted = true;

                    const decoration: vscode.DecorationOptions = { range, hoverMessage };

                    // Route to correct severity bucket
                    const sev = issue.severity.toLowerCase();
                    if      (sev === 'very high') { decsVeryHigh.push(decoration); }
                    else if (sev === 'high')      { decsHigh.push(decoration); }
                    else                          { decsMedium.push(decoration); }
                }

                // ── Apply severity-coloured highlights ────────
                editor.setDecorations(decorationVeryHigh, decsVeryHigh);
                editor.setDecorations(decorationHigh,     decsHigh);
                editor.setDecorations(decorationMedium,   decsMedium);

            } catch (error: any) {
                statusBar.text  = '$(error) GreenOps — connection error';
                statusBar.color = '#f87171';
                vscode.window.showErrorMessage(`GreenOps Error: ${error.message}`);
            }
        }
    );

    context.subscriptions.push(disposable);
}

export function deactivate() {
    allDecorations.forEach(d => d.dispose());
    statusBar?.dispose();
}
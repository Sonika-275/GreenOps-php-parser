import * as vscode from 'vscode';

// ── Types ─────────────────────────────────────────────────────
interface ScalingDetail {
    ec2_delta_usd:           number;
    rds_delta_usd:           number;
    total_scaling_delta_usd: number;
    efficient_ec2_tier:      string;
    degraded_ec2_tier:       string;
    efficient_rds_tier:      string;
    degraded_rds_tier:       string;
    throughput_degradation:  number;
}

interface CostBreakdown {
    rds_io_usd: number;
    scaling:    ScalingDetail | null;
    total_usd:  number;
}

interface CarbonProjections {
    kg_monthly_1x:   number;
    kg_monthly_10x:  number;
    kg_monthly_100x: number;
    kg_annual_1x:    number;
    kg_annual_10x:   number;
    kg_annual_100x:  number;
}

interface Issue {
    rule_id:            string;
    title:              string;
    suggestion:         string;
    line:               number;
    weight:             number;
    severity:           string;
    cost_usd_monthly?:  number;
    cost_inr_monthly?:  number;
    carbon_kg_monthly?: number;
    carbon_projections?: CarbonProjections;
    cost_breakdown?:    CostBreakdown;
    is_throughput_degrader?: boolean;
}

interface AnalyzeResponse {
    green_score:            number;
    estimated_co2_kg:       number;
    issues:                 Issue[];
    total_operation_weight: number;
    total_cost_usd_monthly?: number;
    total_cost_inr_monthly?: number;
}

// ── Constants ─────────────────────────────────────────────────
const DEFAULT_RUNS_PER_DAY         = 10_000;
const USD_TO_INR                   = 84;
const RDS_IO_COST_FLOOR_PER_WEIGHT = 0.000002;

function fallbackMonthlyCostUSD(weight: number): number {
    return weight * RDS_IO_COST_FLOOR_PER_WEIGHT * DEFAULT_RUNS_PER_DAY * 30;
}

// ── Formatting ────────────────────────────────────────────────
function fmtINR(amount: number): string {
    if (amount >= 100_000) return `₹${(amount / 100_000).toFixed(1)}L`;
    if (amount >= 1_000)   return `₹${(amount / 1_000).toFixed(1)}k`;
    return `₹${amount.toFixed(2)}`;
}

function fmtUSD(amount: number): string {
    if (amount >= 1000) return `$${(amount / 1000).toFixed(1)}k`;
    if (amount >= 1)    return `$${amount.toFixed(2)}`;
    return `$${amount.toFixed(4)}`;
}

function fmtCarbon(kg: number): string {
    if (kg >= 1000) return `${(kg / 1000).toFixed(2)} tonnes CO₂`;
    if (kg >= 1)    return `${kg.toFixed(2)} kg CO₂`;
    return `${(kg * 1000).toFixed(2)} g CO₂`;
}

function severityIcon(severity: string): string {
    switch (severity.toLowerCase()) {
        case 'very high': return '🔴';
        case 'high':      return '🟠';
        case 'medium':    return '🟡';
        default:          return '🟢';
    }
}

// ── Decoration Types ──────────────────────────────────────────
const decorationVeryHigh = vscode.window.createTextEditorDecorationType({
    backgroundColor: 'rgba(255, 0, 0, 0.12)',
    border:          '1px solid rgba(255, 80, 80, 0.6)',
    isWholeLine:     true
});

const decorationHigh = vscode.window.createTextEditorDecorationType({
    backgroundColor: 'rgba(255, 140, 0, 0.10)',
    border:          '1px solid rgba(255, 140, 0, 0.5)',
    isWholeLine:     true
});

const decorationMedium = vscode.window.createTextEditorDecorationType({
    backgroundColor: 'rgba(255, 200, 0, 0.08)',
    border:          '1px solid rgba(255, 200, 0, 0.4)',
    isWholeLine:     true
});

const allDecorations = [decorationVeryHigh, decorationHigh, decorationMedium];

// ── Status Bar ────────────────────────────────────────────────
let statusBar: vscode.StatusBarItem;

export function activate(context: vscode.ExtensionContext) {

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

            // ── Server URL — reads from VSCode settings, defaults to localhost ──
            const config  = vscode.workspace.getConfiguration('greenops');
            const baseUrl = config.get<string>('serverUrl') ?? 'http://localhost:8000';

            try {
                const response = await fetch(
                    `${baseUrl}/analyze`,
                    {
                        method:  'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body:    JSON.stringify({ code })
                    }
                );

                if (!response.ok) { throw new Error(`Server error (${response.status})`); }

                const data       = await response.json() as AnalyzeResponse;
                const issueCount = data.issues.length;
                const score      = Math.round(data.green_score);

                // ── Total monthly cost ────────────────────────
                const totalMonthlyUSD = data.total_cost_usd_monthly
                    ?? data.issues.reduce((sum, i) =>
                        sum + (i.cost_usd_monthly ?? fallbackMonthlyCostUSD(i.weight)), 0);
                const totalMonthlyINR = totalMonthlyUSD * USD_TO_INR;

                // ── Status bar ────────────────────────────────
                const scoreIcon   = score >= 70 ? '$(pass)' : score >= 40 ? '$(warning)' : '$(error)';
                statusBar.text    = `${scoreIcon} Score: ${score} | ${fmtINR(totalMonthlyINR)}/mo | ${issueCount} issue${issueCount !== 1 ? 's' : ''}`;
                statusBar.tooltip = [
                    `Monthly infra waste: ${fmtINR(totalMonthlyINR)} (${fmtUSD(totalMonthlyUSD)})`,
                    `Carbon: ${fmtCarbon(data.estimated_co2_kg)}/month`,
                    `At 10x scale: ${fmtINR(totalMonthlyINR * 10)}/month`,
                    `Click to re-analyse`,
                ].join('\n');
                statusBar.color   = score >= 70 ? '#4ade80' : score >= 40 ? '#facc15' : '#f87171';

                vscode.window.showInformationMessage(
                    `GreenOps · Score ${score}/100 · ${fmtINR(totalMonthlyINR)}/month infra waste · ${issueCount} inefficienc${issueCount !== 1 ? 'ies' : 'y'}`
                );

                // ── Clear old highlights ──────────────────────
                allDecorations.forEach(d => editor.setDecorations(d, []));

                const decsVeryHigh: vscode.DecorationOptions[] = [];
                const decsHigh:     vscode.DecorationOptions[] = [];
                const decsMedium:   vscode.DecorationOptions[] = [];

                for (const issue of data.issues) {

                    const lineIndex = issue.line - 1;
                    if (lineIndex < 0 || lineIndex >= editor.document.lineCount) { continue; }

                    const lineText = editor.document.lineAt(lineIndex).text;
                    const range    = new vscode.Range(lineIndex, 0, lineIndex, lineText.length);
                    const icon     = severityIcon(issue.severity);

                    // ── Cost figures ──────────────────────────
                    const monthlyUSD = issue.cost_usd_monthly
                        ?? fallbackMonthlyCostUSD(issue.weight);
                    const monthlyINR = monthlyUSD * USD_TO_INR;
                    const annualUSD  = monthlyUSD * 12;
                    const annualINR  = annualUSD * USD_TO_INR;

                    // ── Carbon figures ────────────────────────
                    const carbonMonthly = issue.carbon_kg_monthly ?? data.estimated_co2_kg;
                    const proj          = issue.carbon_projections;

                    // ── Scaling / tier info ───────────────────
                    const scaling = issue.cost_breakdown?.scaling;

                    // ── Build hover markdown ──────────────────
                    let hoverLines: string[] = [];

                    hoverLines.push(`## ${icon} ${issue.title}`);
                    hoverLines.push('');

                    if (issue.is_throughput_degrader) {
                        hoverLines.push(`> ⚡ **Throughput degrader** — no direct AWS bill line, but burns EC2 CPU that could serve other requests`);
                    } else {
                        hoverLines.push(`> 💸 **Monthly infra waste: ${fmtINR(monthlyINR)}** *(${fmtUSD(monthlyUSD)}) at ${DEFAULT_RUNS_PER_DAY.toLocaleString()} req/day*`);
                    }
                    hoverLines.push('');

                    hoverLines.push('| | |');
                    hoverLines.push('|---|---|');
                    hoverLines.push(`| **Severity** | ${issue.severity} |`);

                    if (!issue.is_throughput_degrader) {
                        hoverLines.push(`| **Monthly waste** | ${fmtINR(monthlyINR)} / ${fmtUSD(monthlyUSD)} |`);
                        hoverLines.push(`| **Annual waste** | ${fmtINR(annualINR)} / ${fmtUSD(annualUSD)} |`);
                    }

                    if (scaling && scaling.total_scaling_delta_usd > 0) {
                        hoverLines.push(`| **EC2 tier pressure** | ${scaling.efficient_ec2_tier} → ${scaling.degraded_ec2_tier} (+$${scaling.ec2_delta_usd}/mo) |`);
                        hoverLines.push(`| **RDS tier pressure** | ${scaling.efficient_rds_tier} → ${scaling.degraded_rds_tier} (+$${scaling.rds_delta_usd}/mo) |`);
                        hoverLines.push(`| **Throughput loss** | ${scaling.throughput_degradation}× more capacity needed |`);
                    }

                    hoverLines.push('');
                    hoverLines.push('**Carbon impact (India CEA 2023 grid · 0.708 kg CO₂/kWh)**');
                    hoverLines.push('');
                    hoverLines.push('| Scale | Monthly | Annual |');
                    hoverLines.push('|---|---|---|');

                    if (proj) {
                        hoverLines.push(`| Current (1×) | ${fmtCarbon(proj.kg_monthly_1x)} | ${fmtCarbon(proj.kg_annual_1x)} |`);
                        hoverLines.push(`| 10× growth | ${fmtCarbon(proj.kg_monthly_10x)} | ${fmtCarbon(proj.kg_annual_10x)} |`);
                        hoverLines.push(`| 100× growth | ${fmtCarbon(proj.kg_monthly_100x)} | ${fmtCarbon(proj.kg_annual_100x)} |`);
                    } else {
                        hoverLines.push(`| Current (1×) | ${fmtCarbon(carbonMonthly)} | ${fmtCarbon(carbonMonthly * 12)} |`);
                        hoverLines.push(`| 10× growth | ${fmtCarbon(carbonMonthly * 10)} | ${fmtCarbon(carbonMonthly * 120)} |`);
                        hoverLines.push(`| 100× growth | ${fmtCarbon(carbonMonthly * 100)} | ${fmtCarbon(carbonMonthly * 1200)} |`);
                    }

                    hoverLines.push('');
                    hoverLines.push(`💡 **Fix:** ${issue.suggestion}`);
                    hoverLines.push('');
                    hoverLines.push('---');
                    hoverLines.push('*GreenOps · EC2+RDS ap-south-1 model · SEBI BRSR Scope 3 ready*');

                    const hoverMessage = new vscode.MarkdownString(hoverLines.join('\n'));
                    hoverMessage.isTrusted = true;

                    const decoration: vscode.DecorationOptions = { range, hoverMessage };

                    const sev = issue.severity.toLowerCase();
                    if      (sev === 'very high') { decsVeryHigh.push(decoration); }
                    else if (sev === 'high')      { decsHigh.push(decoration); }
                    else                          { decsMedium.push(decoration); }
                }

                editor.setDecorations(decorationVeryHigh, decsVeryHigh);
                editor.setDecorations(decorationHigh,     decsHigh);
                editor.setDecorations(decorationMedium,   decsMedium);

            } catch (error: any) {
                statusBar.text  = '$(error) GreenOps — connection error';
                statusBar.color = '#f87171';
                vscode.window.showErrorMessage(`GreenOps Error: ${error.message} — is the server running at ${vscode.workspace.getConfiguration('greenops').get<string>('serverUrl') ?? 'http://localhost:5000'}?`);
            }
        }
    );

    context.subscriptions.push(disposable);
}

export function deactivate() {
    allDecorations.forEach(d => d.dispose());
    statusBar?.dispose();
}
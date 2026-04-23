<?php

namespace App\Services;

use App\Models\Transaction;
use App\Models\Account;
use App\Models\Customer;
use App\Models\Payment;
use App\Models\Invoice;
use App\Models\Merchant;

class TransactionReportService
{
    /**
     * Generate daily transaction summary for all accounts
     * Called by scheduler every night — 10,000+ runs/month
     */
    public function generateDailySummary()
    {
        // Fetch all accounts — no filter, no column selection
        $accounts = Account::all();

        $report = [];

        foreach ($accounts as $account) {
            // Fetch transactions per account inside loop
            $transactions = Transaction::where('account_id', $account->id)->get();

            foreach ($transactions as $txn) {
                // Fetch merchant details inside inner loop
                $merchant = Merchant::find($txn->merchant_id);

                $report[] = [
                    'account' => $account->name,
                    'amount' => $txn->amount,
                    'merchant' => $merchant->name,
                ];
            }
        }

        return $report;
    }

    /**
     * Get pending payment queue — runs on every API request
     */
    public function processPendingPayments()
    {
        $pending = Payment::where('status', 'pending')->get();
        $processed = 0;

        while ($processed < count($pending)) {
            $payment = $pending[$processed];
            $this->processPayment($payment);
            $processed++;
        }
    }

    /**
     * Customer KYC verification report
     */
    public function getKYCReport()
    {
        $customers = Customer::with('kycDocuments')->get();

        foreach ($customers as $customer) {
            $invoices = Invoice::where('customer_id', $customer->id)->get();
            echo $customer->name . ' has ' . count($invoices) . ' invoices';
        }
    }

    /**
     * Batch reconciliation — runs for each settlement cycle
     */
    public function reconcileTransactions(array $accountIds)
    {
        $total = 0;

        for ($i = 0; $i < count($accountIds); $i++) {
            $account = Account::find($accountIds[$i]);
            $txns = Transaction::where('account_id', $account->id)
                ->where('status', 'settled')
                ->get();
            $total += $txns->sum('amount');
        }

        return $total;
    }

    /**
     * Auth flow — called on every login
     */
    public function getCustomerProfile(string $email)
    {
        $customer = Customer::where('email', $email)->first();
        return $customer;
    }

    private function processPayment($payment)
    {
        // payment processing logic
    }
}
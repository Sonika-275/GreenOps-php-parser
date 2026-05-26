<?php

/**
 * test_false_positives.php
 * All patterns below should NOT be flagged by GreenOps after fixes.
 * If any are tagged — it's a false positive bug.
 */

class TestFalsePositives
{

    // ── CATEGORY 1: Non-DB Facades ────────────────────────────

    public function testCacheGet()
    {
        // Cache::get() — not a DB call
        $value = Cache::get('key');
    }

    public function testCacheLock()
    {
        // Cache::lock()->get() — transaction lock, not DB
        $value = Cache::lock('key')->get();
    }

    public function testSessionGet()
    {
        // Session::get() — not a DB call
        $value = Session::get('user_id');
    }

    public function testRedis()
    {
        // Redis::get() — not a DB call
        $value = Redis::get('key');
    }

    public function testCookieGet()
    {
        // Cookie::get() — not a DB call
        $value = Cookie::get('token');
    }

    public function testHttpGet()
    {
        // Http::withHeaders()->get() — HTTP client, not DB
        $response = Http::withHeaders(['Accept' => 'application/json'])->get('https://api.example.com');
    }

    public function testFileGet()
    {
        // File::get() — filesystem, not DB
        $contents = File::get(public_path('img/logo.png'));
    }

    public function testStorageGet()
    {
        // Storage::get() — filesystem, not DB
        $file = Storage::get('uploads/file.pdf');
    }

    public function testArrGet()
    {
        // Arr::get() — array helper, not DB
        $value = Arr::get($data, 'key', 'default');
    }

    public function testSelfGet()
    {
        // self::get() — self reference, not DB
        $value = self::getSomething();
    }

    // ── CATEGORY 2: Pattern Mismatches ───────────────────────

    public function testLockForUpdate()
    {
        // lockForUpdate() — intentional transaction lock
        $ride = \DB::table('rides')
            ->where('id', $id)
            ->where('status', 'pending')
            ->lockForUpdate()
            ->first();
    }

    public function testSharedLock()
    {
        // sharedLock() — intentional lock
        $user = User::where('id', $id)->sharedLock()->first();
    }

    public function testGetThenFirst()
    {
        // ->get()->first() — in-memory collection operation, not DB terminal
        $users = User::select('id', 'name')->where('active', 1)->get()->first();
    }

    public function testGetThenFilter()
    {
        // ->get()->filter() — in-memory collection operation
        $users = User::select('id', 'name')->get()->filter(fn($u) => $u->active);
    }

    public function testSelectDbRaw()
    {
        // select(DB::raw()) — valid select, should not be flagged
        $rides = Ride::select(DB::raw('count(*) as total, status'))
            ->groupBy('status')
            ->get();
    }

    public function testSelectRaw()
    {
        // selectRaw() — valid select
        $results = User::selectRaw('id, name, count(*) as total')
            ->groupBy('id')
            ->get();
    }

    public function testAddSelect()
    {
        // addSelect() — valid select
        $users = User::addSelect('id', 'name')->where('active', 1)->get();
    }

    // ── CATEGORY 2: DB::table() — not Eloquent ───────────────

    public function testDbTableNotEloquent()
    {
        // DB::table() with select — already optimised, should not flag
        $rides = \DB::table('rides')
            ->select('id', 'status', 'driver_id')
            ->where('status', 'active')
            ->get();
    }

    public function testDbSelectRaw()
    {
        // DB::select() — raw SQL, not query builder
        $results = \DB::select('SELECT id, name FROM users WHERE active = ?', [1]);
    }

    // ── CATEGORY 4: Severity Context ─────────────────────────

    public function testFirstStandalone()
    {
        // first() standalone — should be LOW severity (not medium)
        // this IS a true positive but should show as low severity
        $user = User::where('email', $email)->first();
        return $user;
    }

    public function testEagerLoadWithFirst()
    {
        // eager load + first() — should be MEDIUM severity (not high)
        // this IS a true positive but should show as medium severity
        $user = User::with('vehicle')->where('id', $id)->first();
        return $user;
    }

}
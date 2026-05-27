<?php

/**
 * test_false_positives_v2.php
 * 
 * SECTION A — Should NOT be tagged (false positive fixes)
 * SECTION B — Should be tagged (true positives with correct severity)
 */

class TestFalsePositivesV2
{

    // ════════════════════════════════════════════════════════
    // SECTION A — SHOULD NOT BE TAGGED
    // ════════════════════════════════════════════════════════

    // ── Backslash prefix facades ──────────────────────────

    public function testBackslashSession()
    {
        // \Session:: — backslash prefix, not DB
        $userId = \Session::get('user_id');
    }

    public function testBackslashCache()
    {
        // \Cache:: — backslash prefix, not DB
        $value = \Cache::get('key');
    }

    public function testBackslashRedis()
    {
        // \Redis:: — backslash prefix, not DB
        $value = \Redis::get('driver_location');
    }

    public function testFullyQualifiedHttp()
    {
        // \Illuminate\Support\Facades\Http:: — fully qualified, not DB
        $response = \Illuminate\Support\Facades\Http::get('https://api.example.com');
    }

    public function testFullyQualifiedHttpWithHeaders()
    {
        // \Illuminate\Support\Facades\Http::withHeaders()->get()
        $response = \Illuminate\Support\Facades\Http::withHeaders([
            'Accept' => 'application/json'
        ])->get('https://api.example.com/rides');
    }

    public function testBackslashDB()
    {
        // \DB::table() with select — already optimised, not Eloquent
        $rides = \DB::table('rides')
            ->select('id', 'status', 'driver_id')
            ->where('status', 'active')
            ->get();
    }

    // ── lockForUpdate — transaction lock ──────────────────

    public function testLockForUpdate()
    {
        // RideRequest::where()->where()->lockForUpdate()->first()
        // intentional transaction lock — should NOT be flagged
        $rideRequest = RideRequest::where('id', $id)
            ->where('status', 'pending')
            ->lockForUpdate()
            ->first();
    }

    public function testSharedLock()
    {
        // sharedLock() — intentional lock
        $ride = Ride::where('id', $id)->sharedLock()->first();
    }

    public function testLockForShare()
    {
        // lockForShare() — intentional lock
        $payment = Payment::where('ride_id', $id)->lockForShare()->first();
    }

    // ── select(DB::raw()) — valid select ──────────────────

    public function testSelectDbRaw()
    {
        // select(DB::raw()) — valid column selection
        $rides = Ride::select(DB::raw('count(*) as total, status'))
            ->groupBy('status')
            ->get();
    }

    public function testSelectDbRawOne()
    {
        // select(DB::raw(1)) — existence check pattern
        $exists = Ride::whereIn('id', $ids)
            ->whereNotExists(function ($q) {
                $q->select(DB::raw(1))
                    ->from('cancelled_rides')
                    ->whereColumn('ride_id', 'rides.id');
            })
            ->get();
    }

    public function testSelectRaw()
    {
        // selectRaw() — valid select
        $results = Driver::selectRaw('id, name, count(*) as total_rides')
            ->groupBy('id', 'name')
            ->get();
    }

    public function testAddSelect()
    {
        // addSelect() — valid select
        $users = User::addSelect('id', 'name', 'mobile_no')
            ->where('active', 1)
            ->get();
    }

    // ── get()->first() — in-memory collection ops ─────────

    public function testGetThenFirst()
    {
        // ->get()->first() — in-memory, not DB terminal
        $driver = Driver::select('id', 'name')->where('active', 1)->get()->first();
    }

    public function testGetThenFilter()
    {
        // ->get()->filter() — in-memory
        $drivers = Driver::select('id', 'name')->get()->filter(fn($d) => $d->active);
    }

    public function testGetThenMap()
    {
        // ->get()->map() — in-memory
        $names = User::select('id', 'name')->get()->map(fn($u) => $u->name);
    }

    // ── Cache / Session / Redis ───────────────────────────

    public function testCacheGet()
    {
        $value = Cache::get('app_settings');
    }

    public function testCacheLockGet()
    {
        // Cache::lock()->get() — lock acquisition not DB
        $result = Cache::lock('ride_lock')->get();
    }

    public function testSessionGet()
    {
        $userId = Session::get('user_id');
    }

    public function testRedisGet()
    {
        $location = Redis::get('driver:123:location');
    }

    public function testCookieGet()
    {
        $token = Cookie::get('auth_token');
    }

    public function testFileGet()
    {
        $contents = File::get(public_path('img/logo.png'));
    }

    public function testStorageGet()
    {
        $file = Storage::get('uploads/document.pdf');
    }

    public function testArrGet()
    {
        $value = Arr::get($data, 'driver.name', 'unknown');
    }

    public function testHttpGet()
    {
        $response = Http::withHeaders(['Accept' => 'application/json'])
            ->get('https://maps.googleapis.com/api');
    }

    // ════════════════════════════════════════════════════════
    // SECTION B — SHOULD BE TAGGED (true positives)
    // ════════════════════════════════════════════════════════

    public function testTruePositiveGetNoSelect()
    {
        // R3 C2 — get() without select → MEDIUM severity
        $drivers = Driver::where('active', 1)->where('city', 'Salem')->get();
    }

    public function testTruePositiveFirstNoSelect()
    {
        // R3 C3 — first() without select → LOW severity
        $user = User::where('email', $email)->first();
    }

    public function testTruePositiveEagerLoadNoSelect()
    {
        // R3 C4 — with() without select → HIGH severity
        $user = User::with('vehicle')->where('id', $id)->get();
    }

    public function testTruePositiveEagerLoadFirstNoSelect()
    {
        // R3 C4 — with() + first() without select → MEDIUM severity
        $user = User::with('vehicle')->where('id', $id)->first();
    }

    public function testTruePositiveGetInLoop()
    {
        // R3 C5 — get() inside loop → VERY HIGH severity
        foreach ($categories as $cat) {
            $drivers = Driver::where('category_id', $cat->id)->get();
        }
    }

    public function testTruePositiveN1InLoop()
    {
        // R1 — static model call inside foreach → HIGH severity
        foreach ($rides as $ride) {
            $driver = Driver::find($ride->driver_id);
        }
    }

    public function testTruePositiveDbTableNoSelect()
    {
        // R3 C6 — DB::table()->get() without select → MEDIUM severity
        $rides = \DB::table('rides')
            ->where('status', 'active')
            ->orderBy('created_at', 'desc')
            ->get();
    }

    public function testTruePositiveDbTableFirstNoSelect()
    {
        // R3 C7 — DB::table()->first() without select → LOW severity
        $setting = \DB::table('settings')
            ->where('key', 'pickup_radius')
            ->first();
    }

    public function testTruePositiveSelfWhereFirst()
    {
        // self:: Eloquent call — true positive → LOW severity
        $openSession = self::where('driver_id', $driverId)
            ->whereNull('ended_at')
            ->first();
    }

}
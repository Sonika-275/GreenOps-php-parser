<?php

class TestLockForUpdate
{
    // ── Should NOT be tagged ──────────────────────────────────

    public function test1()
    {
        // basic lockForUpdate
        $ride = Ride::where('id', $id)->lockForUpdate()->first();
    }

    public function test2()
    {
        // multiple where + lockForUpdate
        $rideRequest = RideRequestResponse::where('id', $id)
            ->where('status', 'pending')
            ->lockForUpdate()
            ->first();
    }

    public function test3()
    {
        // sharedLock
        $payment = Payment::where('ride_id', $id)->sharedLock()->first();
    }

    public function test4()
    {
        // lockForUpdate with get()
        $rides = Ride::where('status', 'active')->lockForUpdate()->get();
    }

    public function test5()
    {
        // lockForShare
        $user = User::where('id', $id)->lockForShare()->first();
    }

    public function test5b()
    {
        // orderBy + lockForUpdate
        $ride = Ride::where('status', 'active')
            ->orderBy('created_at', 'desc')
            ->lockForUpdate()
            ->first();
    }

    public function test5c()
    {
        // multiple where + orderBy + lockForUpdate + get()
        $rides = RideRequest::where('driver_id', $driverId)
            ->where('status', 'pending')
            ->orderBy('created_at')
            ->lockForUpdate()
            ->get();
    }

    // ── Should be tagged (no lock, no select) ─────────────────

    public function test6()
    {
        // no lock — should tag as R3 low severity
        $ride = Ride::where('id', $id)->first();
    }

    public function test7()
    {
        // no lock — should tag as R3 medium severity
        $rides = Ride::where('status', 'active')->get();
    }
}
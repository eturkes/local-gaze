import GLib from 'gi://GLib';

// Token-bucket backstop on the extension side (the daemon enforces its own
// global ceiling; this is defense-in-depth against action spam if the daemon
// mis-tunes or is bypassed by another same-UID caller). Lazy refill keyed off
// the monotonic clock — no periodic timer is required, but any timer id ever
// created is tracked so teardown can remove it.
export class RateLimiter {
    constructor(ratePerSec, burst = ratePerSec) {
        this._rate = Math.max(ratePerSec, 0);
        this._capacity = Math.max(burst, 1);
        this._tokens = this._capacity;
        this._last = GLib.get_monotonic_time();
        this._timers = [];
    }

    _refill() {
        const now = GLib.get_monotonic_time();
        const dt = (now - this._last) / 1.0e6;
        if (dt <= 0) {
            return;
        }
        this._last = now;
        this._tokens = Math.min(this._capacity, this._tokens + dt * this._rate);
    }

    // Consume one token; returns true if the action is allowed.
    allow() {
        if (this._rate <= 0) {
            return true;
        }
        this._refill();
        if (this._tokens >= 1) {
            this._tokens -= 1;
            return true;
        }
        return false;
    }

    destroy() {
        for (const id of this._timers) {
            GLib.Source.remove(id);
        }
        this._timers = [];
    }
}

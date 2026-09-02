/*
 * race-animation-audio.js — the sound bed for the Race Animations page.
 *
 * Loaded only by templates/race-animations-predictions.html, alongside
 * race-animation.js, and handed to the engine as options.audio.
 *
 * WHY IT IS SYNTHESISED
 * There are no audio files. Every cue here is built out of oscillators and a
 * short buffer of noise at run time, which means no megabytes of hoofbeat
 * samples to ship, nothing to 404, and no licence to worry about. It also
 * means the hoofbeats can genuinely change tempo with the race instead of a
 * loop being played faster.
 *
 * THE CUES
 *   gate      one clang as the barriers open, on play from a standing start
 *   hoofbeats a four-beat gallop under the whole race, its tempo and its
 *             volume driven by how much of the field is still with the leader
 *   crowd     a low murmur bed all race, rising through the run home
 *   roar      the crowd let go as the winner hits the post
 *
 * AUTOPLAY
 * Browsers will not let a page make a noise until somebody has clicked
 * something, and a page that tries gets its whole audio context suspended.
 * Nothing here builds an AudioContext until unlock() is called, and the page
 * only calls it from the Run the race handler.
 *
 * The engine never waits on this module and never reads anything back from it.
 * If the browser has no Web Audio at all, create() returns a handle whose
 * methods do nothing, and the race runs exactly as it did before.
 */
(function (global) {
    'use strict';

    function Context() {
        return global.AudioContext || global.webkitAudioContext || null;
    }

    /* Is Web Audio available at all? Checked rather than assumed: this is the
     * one part of the page that is allowed to be missing entirely. */
    function supported() {
        return !!Context();
    }

    /* A second of white noise, made once and shared by every voice that needs
     * it. Hooves, crowd and the gate clang are all noise through different
     * filters — generating a fresh buffer per voice would be the same numbers
     * three times over. */
    function noiseBuffer(ctx) {
        var buffer = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
        var data = buffer.getChannelData(0);
        for (var i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
        return buffer;
    }

    /* create(options)
     *   muted      start muted (the page defaults this on under reduced motion)
     *   volume     master gain when unmuted, 0..1
     *
     * Returns the handle the engine calls:
     *   unlock()             build the context, from a user gesture only
     *   play(raceTime)       the race is running from raceTime
     *   pause()              the race has stopped but is not over
     *   stop()               tear the whole thing down
     *   update(t, bunching)  once a frame: race clock, and how much of the
     *                        field is still with the leader (0..1)
     *   finish()             the winner has hit the post
     *   setMuted(on) / isMuted()
     */
    function create(options) {
        options = options || {};
        var muted = options.muted != null ? !!options.muted : false;
        var volume = options.volume != null ? options.volume : 0.5;

        var ctx = null;
        var master = null;      // everything goes through here, including mute
        var crowdGain = null;   // the murmur bed
        var hoofGain = null;    // the gallop
        var noise = null;
        var running = false;    // is the race meant to be making noise?
        var beatAt = 0;         // when the next hoofbeat is due, in ctx time
        var beat = 0;           // which of the four beats that is
        var tempo = 2.1;        // strides a second
        var loudness = 0.5;     // how much of the field is in the bunch
        var ramped = {};        // last value each gain was sent to — see ramp()

        function live() { return !!ctx && ctx.state !== 'closed'; }

        /* Build the graph. Only ever called from unlock(), which the page only
         * ever calls from a click. */
        function build() {
            var Ctor = Context();
            if (!Ctor) return false;
            ctx = new Ctor();
            noise = noiseBuffer(ctx);
            ramped = {};

            master = ctx.createGain();
            master.gain.value = muted ? 0 : volume;
            master.connect(ctx.destination);

            // The crowd: noise pushed through a low-pass so it is a hum from
            // the far side of the track rather than a hiss in the room.
            var crowdSource = ctx.createBufferSource();
            crowdSource.buffer = noise;
            crowdSource.loop = true;
            var crowdFilter = ctx.createBiquadFilter();
            crowdFilter.type = 'lowpass';
            crowdFilter.frequency.value = 620;
            crowdFilter.Q.value = 0.6;
            crowdGain = ctx.createGain();
            crowdGain.gain.value = 0;
            crowdSource.connect(crowdFilter);
            crowdFilter.connect(crowdGain);
            crowdGain.connect(master);
            crowdSource.start(0);

            hoofGain = ctx.createGain();
            hoofGain.gain.value = 0;
            hoofGain.connect(master);
            return true;
        }

        /* One hoof strike: a very short burst of band-passed noise with a hard
         * attack and a fast decay, which is what a hoof on turf sounds like
         * once everything above 400Hz has been taken off it. */
        function strike(at, level, pitch) {
            if (!live()) return;
            var source = ctx.createBufferSource();
            source.buffer = noise;
            var filter = ctx.createBiquadFilter();
            filter.type = 'bandpass';
            filter.frequency.value = pitch;
            filter.Q.value = 1.1;
            var envelope = ctx.createGain();
            envelope.gain.setValueAtTime(0, at);
            envelope.gain.linearRampToValueAtTime(level, at + 0.004);
            envelope.gain.exponentialRampToValueAtTime(0.0001, at + 0.11);
            source.connect(filter);
            filter.connect(envelope);
            envelope.connect(hoofGain);
            source.start(at, Math.random() * 0.4, 0.14);
            source.stop(at + 0.14);
        }

        /* The barrier clang. Two detuned square waves ringing through a
         * bandpass, plus a noise slap underneath for the gate itself. */
        function clang() {
            if (!live()) return;
            var at = ctx.currentTime + 0.01;
            var body = ctx.createGain();
            body.gain.setValueAtTime(0.0001, at);
            body.gain.exponentialRampToValueAtTime(0.5, at + 0.006);
            body.gain.exponentialRampToValueAtTime(0.0001, at + 0.85);
            body.connect(master);

            [1180, 1790].forEach(function (frequency, index) {
                var tone = ctx.createOscillator();
                tone.type = 'square';
                tone.frequency.value = frequency;
                var voice = ctx.createGain();
                voice.gain.value = index ? 0.35 : 0.6;
                tone.connect(voice);
                voice.connect(body);
                tone.start(at);
                tone.stop(at + 0.9);
            });

            var slap = ctx.createBufferSource();
            slap.buffer = noise;
            var filter = ctx.createBiquadFilter();
            filter.type = 'highpass';
            filter.frequency.value = 900;
            var envelope = ctx.createGain();
            envelope.gain.setValueAtTime(0.5, at);
            envelope.gain.exponentialRampToValueAtTime(0.0001, at + 0.18);
            slap.connect(filter);
            filter.connect(envelope);
            envelope.connect(master);
            slap.start(at, 0, 0.2);
            slap.stop(at + 0.2);
        }

        /* The roar. The murmur bed swells and a wash of brighter noise comes
         * up over the top of it, then both fall away. */
        function roar() {
            if (!live()) return;
            var at = ctx.currentTime;
            var source = ctx.createBufferSource();
            source.buffer = noise;
            source.loop = true;
            var filter = ctx.createBiquadFilter();
            filter.type = 'bandpass';
            filter.frequency.setValueAtTime(500, at);
            filter.frequency.linearRampToValueAtTime(1250, at + 0.5);
            filter.Q.value = 0.5;
            var envelope = ctx.createGain();
            envelope.gain.setValueAtTime(0.0001, at);
            envelope.gain.linearRampToValueAtTime(0.55, at + 0.28);
            envelope.gain.exponentialRampToValueAtTime(0.0001, at + 3.2);
            source.connect(filter);
            filter.connect(envelope);
            envelope.connect(master);
            source.start(at);
            source.stop(at + 3.3);
        }

        /* Glide a gain to a new value.
         *
         * Guarded against being handed the value it is already heading for:
         * update() runs sixty times a second and the bunching barely moves
         * between frames, so without this the graph would be carrying a
         * thousand automation events a race for no audible difference. */
        function ramp(name, param, value, seconds) {
            if (!live()) return;
            if (ramped[name] != null && Math.abs(ramped[name] - value) < 0.01) return;
            ramped[name] = value;
            var now = ctx.currentTime;
            param.cancelScheduledValues(now);
            param.setValueAtTime(param.value, now);
            param.linearRampToValueAtTime(value, now + (seconds || 0.2));
        }

        var handle = {
            supported: supported,

            /* Called from the page's click handler and nowhere else. Building
             * the context here rather than at load is the whole autoplay
             * story: a context created outside a gesture starts suspended and
             * stays that way. */
            unlock: function () {
                if (!live() && !build()) return false;
                if (ctx.state === 'suspended' && ctx.resume) ctx.resume();
                return true;
            },

            play: function (raceTime) {
                if (!live()) return;
                if (ctx.state === 'suspended' && ctx.resume) ctx.resume();
                running = true;
                beatAt = ctx.currentTime;
                // A clang belongs to a race that is starting, not to one being
                // un-paused halfway down the back straight.
                if (!raceTime) clang();
                ramp('crowd', crowdGain.gain, 0.1, 0.6);
            },

            pause: function () {
                if (!live()) return;
                running = false;
                ramp('hoof', hoofGain.gain, 0, 0.25);
                ramp('crowd', crowdGain.gain, 0, 0.5);
            },

            /* Once a frame, straight off the engine's own numbers. `bunching`
             * is the share of the field still within a few lengths of the
             * leader, which is what decides how much noise a race makes: a
             * procession is quiet and a wall of horses is not. */
            update: function (raceTime, bunching) {
                if (!live() || !running) return;
                loudness = bunching == null ? loudness : bunching;
                // Strides a second: a settled gallop, winding up in the run home.
                tempo = 2.05 + 0.75 * (raceTime > 0.65 ? (raceTime - 0.65) / 0.35 : 0);
                var mass = 0.18 + 0.5 * loudness;
                ramp('hoof', hoofGain.gain, mass, 0.3);
                ramp('crowd', crowdGain.gain, 0.08 + 0.16 * (raceTime > 0.6 ? (raceTime - 0.6) / 0.4 : 0), 0.4);

                /* Schedule the beats slightly ahead of the clock rather than
                 * firing one per frame. Frames are not evenly spaced and a
                 * gallop that wanders with the frame rate sounds broken; the
                 * audio clock does not wander. */
                var now = ctx.currentTime;
                if (beatAt < now) beatAt = now;
                while (beatAt < now + 0.2) {
                    // Four beats to the stride, then a gap: the gallop's
                    // suspension, which is what makes it a gallop and not a run.
                    var step = (1 / tempo) * (beat === 3 ? 0.46 : 0.18);
                    var accent = beat === 0 ? 1 : 0.72;
                    strike(beatAt, 0.5 * accent, 190 + beat * 26);
                    beatAt += step;
                    beat = (beat + 1) % 4;
                }
            },

            finish: function () {
                if (!live()) return;
                running = false;
                ramp('hoof', hoofGain.gain, 0, 0.35);
                roar();
            },

            setMuted: function (on) {
                muted = !!on;
                if (live()) ramp('master', master.gain, muted ? 0 : volume, 0.15);
            },

            isMuted: function () { return muted; },

            /* Silence, but stay built.
             *
             * The engine calls this from destroy(), and the page destroys and
             * rebuilds the race every time a weight slider moves. Closing the
             * context there would burn one of the handful a browser will give a
             * page, and the next race would have no sound at all until the
             * viewer happened to click something. So this stops the noise and
             * keeps the graph; close() is the real teardown, and nothing on the
             * page needs it. */
            stop: function () {
                running = false;
                if (!live()) return;
                ramp('hoof', hoofGain.gain, 0, 0.1);
                ramp('crowd', crowdGain.gain, 0, 0.1);
            },

            close: function () {
                running = false;
                if (!live()) return;
                var dying = ctx;
                ctx = null;
                try {
                    if (dying.close) dying.close();
                } catch (error) {
                    /* already gone */
                }
            }
        };
        return handle;
    }

    global.RaceAnimationAudio = {
        create: create,
        supported: supported
    };
}(typeof window !== 'undefined' ? window : this));

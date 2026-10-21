"""One bounded, coalescing executor for model and host validation requests."""
import asyncio
import time


class ValidationService:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.jobs = {}
        self.slots = asyncio.Semaphore(2)
        self.npm_locks = {}

    async def run(self, ident, run_id, check, *, force=False, targets=()):
        p = self.pipeline
        async with p.features.lock(ident):
            feature, run = p.run(ident, run_id); p.require_active(feature, run)
        targets = await asyncio.to_thread(p.checks.targets, feature, check, targets)
        identity = await asyncio.to_thread(p.checks.identity, feature, check, targets)
        async with p.features.lock(ident):
            feature, run = p.run(ident, run_id); p.require_active(feature, run)
            from .manual_approval import accepted_check
            previous = feature['pipeline']['checks'].get(check)
            if (not force and not targets and run['stage'] == 'deployment' and previous
                    and previous.get('cache_key') == identity['cache_key']
                    and accepted_check(feature['pipeline'], check, previous, identity['source_hash'], feature['input_revision'])):
                return self.record(feature, run, {**previous, 'cached': True}, 'approved_check_reused')
            cached = feature['pipeline'].get('check_cache', {}).get(identity['cache_key'])
            if cached and not force:
                return self.record(feature, run, {**cached, 'cached': True}, 'check_reused')
            key = (ident, identity['cache_key'], force)
            job = self.jobs.get(key)
            joined = job is not None
            if job is None:
                job = asyncio.create_task(self.execute(feature, check, identity, targets))
                self.jobs[key] = job
                job.add_done_callback(lambda done: self.jobs.pop(key, None))
        result = await asyncio.shield(job)
        async with p.features.lock(ident):
            feature, run = p.run(ident, run_id); p.require_active(feature, run)
            if result['status'] in ('passed', 'failed') and result.get('cache_key'):
                cache = feature['pipeline'].setdefault('check_cache', {})
                cache[result['cache_key']] = result
                while len(cache) > 24:
                    del cache[next(iter(cache))]
            return self.record(feature, run, {**result, 'cached': joined}, 'check_reused' if joined else 'check')

    async def execute(self, feature, check, identity, targets):
        # npm scripts can share generated files; serialize those per workspace.
        if check in ('types', 'browser_tests', 'build'):
            lock = self.npm_locks.setdefault(feature['worktree'], asyncio.Lock())
            async with lock:
                return await self.execute_check(feature, check, identity, targets)
        return await self.execute_check(feature, check, identity, targets)

    async def execute_check(self, feature, check, identity, targets):
        async with self.slots:
            # Execute against current inputs after acquiring the workspace slot.
            # Waiting behind another check is not itself stale evidence.
            before = await asyncio.to_thread(self.pipeline.checks.identity, feature, check, targets)
            identity = before
            result = await self.pipeline.checks.run(feature, check, targets) if targets else await self.pipeline.checks.run(feature, check)
            after = await asyncio.to_thread(self.pipeline.checks.identity, feature, check, targets)
            if after != before or result.get('source_hash') != before['source_hash']:
                return {**result, 'check': check, 'source_hash': before['source_hash'], 'status': 'stale',
                    'output': result.get('output', '') + '\nValidation inputs changed or were not recorded; this evidence cannot pass the gate.'}
            return {**result, **identity, 'cached': False}

    def record(self, feature, run, result, name):
        if result.get('scope') == 'focused':
            from sdlc.models import digest
            evidence = feature['pipeline'].setdefault('focused_checks', {})
            evidence[digest([result['check'], result['targets']])] = result
            while len(evidence) > 30: del evidence[next(iter(evidence))]
        else:
            feature['pipeline']['checks'][result['check']] = result
        run['trace'].append({'name': name, 'detail': result['check'] + ': ' + result['status'], 'time': time.time()})
        self.pipeline.store.put(feature); self.pipeline.announce(feature['id'])
        return result

    async def stop(self):
        jobs = list(self.jobs.values())
        for job in jobs: job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

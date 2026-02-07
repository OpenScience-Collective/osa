# Deployment Checklist for Epic #97 (EEGLAB)

## Pre-Merge Verification

- [x] All PR review issues addressed (critical + important)
- [x] All tests passing locally
- [x] Database migration added for `branch` column
- [x] No breaking changes to existing APIs

## After Merge to `develop`

### 1. Automatic CI/CD
- [ ] GitHub Actions builds Docker image successfully
- [ ] Image pushed to ghcr.io with `dev` tag
- [ ] Health check passes in CI

**Monitor**: https://github.com/OpenScience-Collective/osa/actions

### 2. Manual Deployment to Dev Server

SSH into the server:
```bash
ssh -J hallu hedtools
cd ~/osa
```

Pull and deploy:
```bash
# Pull latest code (if needed for deploy script updates)
git pull origin develop

# Deploy dev container
./deploy/deploy.sh dev
```

### 3. Verify Deployment

Check container status:
```bash
docker ps | grep osa-dev
docker logs osa-dev --tail 50
```

Expected log output:
- ✅ "Knowledge database initialized at ..."
- ✅ "Migration complete: branch column added to docstrings" (for existing DBs)
- ✅ "Uvicorn running on http://0.0.0.0:38528"
- ✅ No errors or stack traces

Check health endpoint:
```bash
curl https://api.osc.earth/osa-dev/health
```

Expected response:
```json
{
  "status": "healthy",
  "version": "0.5.1.dev0",
  "environment": "development"
}
```

### 4. Verify EEGLAB Community

Check community is registered:
```bash
curl https://api.osc.earth/osa-dev/communities | jq '.[] | select(.id=="eeglab")'
```

Expected response includes:
```json
{
  "id": "eeglab",
  "name": "EEGLAB",
  "description": "EEG signal processing and analysis toolbox",
  "status": "available"
}
```

### 5. Test Database Migration

If dev already has docstring data, verify migration worked:
```bash
docker exec osa-dev python -c "
import sqlite3
from src.knowledge.db import get_db_path

db_path = get_db_path('eeglab')
if db_path.exists():
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute('PRAGMA table_info(docstrings)')
    columns = [row[1] for row in cursor.fetchall()]
    print('✅ branch column exists' if 'branch' in columns else '❌ MIGRATION FAILED')
    conn.close()
else:
    print('ℹ️  No existing database (will be created on first sync)')
"
```

### 6. Sync EEGLAB Data (Optional - Admin Only)

If you want to populate the dev database with real data:

```bash
# Inside the container
docker exec -it osa-dev bash

# Sync docstrings (uses config from config.yaml)
python -m src.cli.main sync docstrings --community eeglab --language matlab

# Sync mailing list (takes ~30 minutes for 2004-2026)
python -m src.cli.main sync mailman --community eeglab --start-year 2024

# Generate FAQs (requires LLM calls)
python -m src.cli.main sync faq --community eeglab --quality 0.7
```

**Note**: These sync commands require API_KEYS environment variable to be set.

### 7. Test Frontend Widget

Visit: https://develop-demo.osc.earth

- [ ] EEGLAB appears in community dropdown
- [ ] Can select EEGLAB community
- [ ] Chat interface loads
- [ ] Can send a test message (e.g., "How do I import EEG data?")
- [ ] Response includes tool invocations
- [ ] No console errors

### 8. Smoke Test API Endpoints

Test ask endpoint:
```bash
curl -X POST https://api.osc.earth/osa-dev/eeglab/ask \
  -H "Content-Type: application/json" \
  -d '{
    "message": "How do I filter my EEG data?",
    "model": "haiku"
  }' | jq -r '.response' | head -30
```

Expected:
- ✅ Response contains helpful information about filtering
- ✅ Response mentions EEGLAB functions or tools
- ✅ No error messages

## Rollback Plan

If deployment fails:

```bash
# Check logs for errors
docker logs osa-dev

# Rollback to previous version
docker stop osa-dev
docker rm osa-dev

# Find previous working image
docker images | grep osa

# Deploy previous version (replace SHA with actual)
docker run -d --name osa-dev \
  --env-file ~/osa/.env \
  -p 38529:38528 \
  ghcr.io/openscience-collective/osa:sha-<PREVIOUS_SHA>
```

## Known Issues & Workarounds

### Issue: Database Locked
If you see "database is locked" errors:
```bash
# Stop container
docker stop osa-dev

# Wait for locks to clear (or reboot if persistent)
sleep 5

# Restart container
docker start osa-dev
```

### Issue: Migration Fails
If branch column migration fails:
```bash
# Manual migration inside container
docker exec -it osa-dev python -c "
import sqlite3
from src.knowledge.db import get_db_path

for community in ['eeglab', 'hed']:
    db_path = get_db_path(community)
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute('ALTER TABLE docstrings ADD COLUMN branch TEXT NOT NULL DEFAULT \"main\"')
            conn.commit()
            print(f'✅ {community}: migration successful')
        except sqlite3.OperationalError as e:
            if 'duplicate column' in str(e).lower():
                print(f'ℹ️  {community}: branch column already exists')
            else:
                print(f'❌ {community}: {e}')
        finally:
            conn.close()
"
```

## Post-Deployment Monitoring

### First 24 Hours
- Monitor error logs: `docker logs osa-dev -f | grep ERROR`
- Check request volume and response times
- Watch for database errors
- Monitor LangFuse dashboard (if enabled) for tool usage

### First Week
- Gather user feedback on EEGLAB assistant
- Monitor sync job performance (if scheduled)
- Check database size growth
- Verify no regression in existing HED community

## Success Criteria

- ✅ Dev container running and healthy
- ✅ EEGLAB community accessible via API
- ✅ Frontend widget works with EEGLAB
- ✅ Database migration completed successfully
- ✅ No errors in logs
- ✅ Existing HED community still works
- ✅ Health endpoint responds correctly

## Contact

If deployment issues arise:
- Check #osa-dev Slack channel
- Review GitHub Actions logs
- SSH to server and check Docker logs
- Rollback if critical issues found

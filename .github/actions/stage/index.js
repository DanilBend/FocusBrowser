const core = require('@actions/core');
const io = require('@actions/io');
const exec = require('@actions/exec');
const {DefaultArtifactClient} = require('@actions/artifact');
const glob = require('@actions/glob');

const path = require('path');
const fs = require('fs/promises');
const { existsSync } = require('fs');
const {parseFocusReleaseVersion} = require('./release-version');

async function getFilesToSign() {
    const ROOT = 'C:\\focus-windows\\build\\src';
    const OUT_PATH = path.join(ROOT, 'out\\Default');
    const MANIFEST_PATH =
        path.join(ROOT, 'infra\\archive_config\\win-archive-rel.json');

    const { archive_datas } =
        JSON.parse(await fs.readFile(MANIFEST_PATH, 'utf8'));

    const fileNames = [...new Set(
        archive_datas.map(archive =>
            (archive.files || []).filter(
                file => file.endsWith('.exe') || file.endsWith('.dll')
            )
        ).flat(1)
    )];

    return fileNames.map(fileName => {
        const absPath = path.join(OUT_PATH, fileName);
        return existsSync(absPath) ? absPath : null;
    }).filter(path => path);
}

async function run() {
    const started_at = Math.floor(new Date() / 1000);

    process.on('SIGINT', function() {
    })
    const from_artifact = core.getBooleanInput('from_artifact', {required: true});
    const gen_installer = core.getBooleanInput('gen_installer', {required: false});
    const upload_final = core.getBooleanInput('upload_final', {required: false});

    const arm = core.getBooleanInput('arm', {required: false})
    console.log(`artifact: ${from_artifact}, gen_installer: ${gen_installer}, upload_final: ${upload_final}`);

    const artifact = new DefaultArtifactClient();
    const artifactName = arm ? 'build-artifact-arm64' : 'build-artifact-x86_64';
    const same_runner = gen_installer || upload_final;

    if (from_artifact && !same_runner) {
        const artifactInfo = await artifact.getArtifact(artifactName);
        await artifact.downloadArtifact(artifactInfo.artifact.id, {path: 'C:\\focus-windows\\build'});
        await exec.exec('7z', ['x', 'C:\\focus-windows\\build\\artifacts.zip',
            '-oC:\\focus-windows\\build', '-y']);
        await io.rmRF('C:\\focus-windows\\build\\artifacts.zip');
    }

    const args = ['build.py', '--ci', String(started_at)]
    if (process.env.RUNNER_ENVIRONMENT === 'github-hosted')
        args.push('-j', '2');

    if (arm)
        args.push('--arm')

    if (gen_installer) {
        core.addPath('C:\\focus-windows\\build\\src\\third_party\\nsis');
        args.push('--build-installer');
    }

    if (upload_final) {
        const globber = await glob.create('C:\\focus-windows\\build\\FocusBrowser_*',
            {matchDirectories: false});
        let packageList = await globber.glob();
        const finalArtifactName = arm ? 'focus-arm64' : 'focus-x86_64';
        for (let i = 0; i < 5; ++i) {
            try {
                await artifact.deleteArtifact(finalArtifactName);
            } catch (e) {
                // ignored
            }
            try {
                await artifact.uploadArtifact(finalArtifactName, packageList,
                    'C:\\focus-windows\\build', { retentionDays: 4, compressionLevel: 0 });
                break;
            } catch (e) {
                console.error(`Upload artifact failed: ${e}`);
                // Wait 10 seconds between the attempts
                await new Promise(r => setTimeout(r, 10000));
            }
        }

        const { exitCode, stdout } = await exec.getExecOutput('python', [
            'focus-chromium\\utils\\focus_version.py',
            '--print',
            '--tree', 'focus-chromium',
            '--platform-tree', '.'
        ]);

        if (exitCode !== 0) throw `failed getting version: ${exitCode}`;
        const releaseVersion = parseFocusReleaseVersion(stdout);
        core.setOutput('version', releaseVersion.fullVersion);
        core.setOutput('display_version', releaseVersion.displayVersion);
        core.setOutput('release_tag', releaseVersion.releaseTag);
        core.setOutput('finished', true);
        return;
    }

    await exec.exec('python', ['-m', 'pip', 'install', 'httplib2==0.22.0', 'Pillow'], {
        cwd: 'C:\\focus-windows',
        ignoreReturnCode: true
    });
    const retCode = await exec.exec('python', args, {
        cwd: 'C:\\focus-windows',
        ignoreReturnCode: true
    });

    if (retCode > 0 && retCode !== 42) {
        throw `Unexpected return code: ${retCode}`
    }

    core.setOutput('finished', retCode === 0);

    let paths = [];
    if (retCode === 0) {
        paths = await getFilesToSign();
        console.log('Files to sign:', paths);
    }
    core.setOutput('files-to-sign', paths.join(','));

    if (!gen_installer) {
        await exec.exec('7z', ['a', '-tzip', 'C:\\focus-windows\\artifacts.zip',
            'C:\\focus-windows\\build\\src', '-mx=3', '-mtc=on'], {ignoreReturnCode: true});
        for (let i = 0; i < 5; ++i) {
            try {
                await artifact.deleteArtifact(artifactName);
            } catch (e) {
                // ignored
            }
            try {
                await artifact.uploadArtifact(artifactName, ['C:\\focus-windows\\artifacts.zip'],
                    'C:\\focus-windows', { retentionDays: 4, compressionLevel: 0 });
                break;
            } catch (e) {
                console.error(`Upload artifact failed: ${e}`);
                // Wait 10 seconds between the attempts
                await new Promise(r => setTimeout(r, 10000));
            }
        }
    }
}

run().catch(err => core.setFailed(err.message));

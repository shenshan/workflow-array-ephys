# run tests: pytest -sv --cov-report term-missing --cov=workflow-ephys -p no:warnings

import os
import pytest
import pandas as pd
import pathlib
import datajoint as dj
import importlib

from workflow_ephys.paths import get_ephys_root_data_dir


@pytest.fixture(autouse=True)
def dj_config():
    dj.config.load('./dj_local_conf.json')
    dj.config['safemode'] = False
    dj.config['custom'] = {
        'database.prefix': os.environ.get('DATABASE_PREFIX', dj.config['custom']['database.prefix']),
        'ephys_root_data_dir': os.environ.get('EPHYS_ROOT_DATA_DIR', dj.config['custom']['ephys_root_data_dir'])
    }
    return


@pytest.fixture
def pipeline():
    from workflow_ephys import pipeline

    yield (pipeline.subject, pipeline.lab, pipeline.ephys,
           pipeline.probe, pipeline.Session, pipeline.get_ephys_root_data_dir)

    pipeline.subject.Subject.delete()


@pytest.fixture
def subjects_csv():
    """ Create a 'subjects.csv' file"""
    input_subjects = pd.DataFrame(columns=['subject', 'sex', 'subject_birth_date', 'subject_description'])
    input_subjects.subject = ['subject1', 'subject2', 'subject3', 'subject4', 'subject5']
    input_subjects.sex = ['F', 'M', 'M', 'M', 'F']
    input_subjects.subject_birth_date = ['2020-01-01 00:00:01', '2020-01-01 00:00:01',
                                         '2020-01-01 00:00:01', '2020-01-01 00:00:01', '2020-01-01 00:00:01']
    input_subjects.subject_description = ['dl56', 'SC035', 'SC038', 'oe_talab', 'rich']

    subjects_csv_fp = pathlib.Path('./tests/user_data/subjects.csv')

    input_subjects.to_csv(subjects_csv_fp)  # write csv file

    yield input_subjects

    subjects_csv_fp.unlink()  # delete csv file after use


@pytest.fixture
def ingest_subjects(pipeline, subjects_csv):
    from workflow_ephys.ingest import ingest_subjects
    ingest_subjects()
    return


@pytest.fixture
def sessions_csv():
    """ Create a 'sessions.csv' file"""
    root_dir = pathlib.Path(get_ephys_root_data_dir())

    sessions_dirs = ['U24/workflow_ephys_data/subject1/session1',
                     'U24/workflow_ephys_data/subject2/session1',
                     'U24/workflow_ephys_data/subject2/session2',
                     'U24/workflow_ephys_data/subject3/session1',
                     'U24/workflow_ephys_data/subject4/experiment1',
                     'U24/workflow_ephys_data/subject5/2018-07-03_19-10-39']

    input_sessions = pd.DataFrame(columns=['subject', 'session_dir'])
    input_sessions.subject = ['subject1',
                              'subject2',
                              'subject2',
                              'subject3',
                              'subject4',
                              'subject5']
    input_sessions.session_dir = [(root_dir / sess_dir).as_posix() for sess_dir in sessions_dirs]

    sessions_csv_fp = pathlib.Path('./tests/user_data/sessions.csv')

    input_sessions.to_csv(sessions_csv_fp)  # write csv file

    yield input_sessions

    sessions_csv_fp.unlink()  # delete csv file after use


@pytest.fixture
def ingest_sessions(ingest_subjects, sessions_csv):
    from workflow_ephys.ingest import ingest_sessions
    ingest_sessions()
    return


@pytest.fixture
def testdata_paths():
    return {
        'npx3A-p0-ks': 'subject5/2018-07-03_19-10-39/probe_1/ks2.1_01',
        'npx3A-p1-ks': 'subject5/2018-07-03_19-10-39/probe_1/ks2.1_01',
        'oe_npx3B-ks': 'subject4/experiment1/recording1/continuous/Neuropix-PXI-100.0/ks',
        'npx3A-p0': 'subject5/2018-07-03_19-10-39/probe_1',
        'oe_npx3B': 'subject4/experiment1/recording1/continuous/Neuropix-PXI-100.0',
    }


@pytest.fixture
def kilosort_paramset(pipeline):
    _, _, ephys, _, _, _ = pipeline

    params_ks = {
        "fs": 30000,
        "fshigh": 150,
        "minfr_goodchannels": 0.1,
        "Th": [10, 4],
        "lam": 10,
        "AUCsplit": 0.9,
        "minFR": 0.02,
        "momentum": [20, 400],
        "sigmaMask": 30,
        "ThPr": 8,
        "spkTh": -6,
        "reorder": 1,
        "nskip": 25,
        "GPU": 1,
        "Nfilt": 1024,
        "nfilt_factor": 4,
        "ntbuff": 64,
        "whiteningRange": 32,
        "nSkipCov": 25,
        "scaleproc": 200,
        "nPCs": 3,
        "useRAM": 0
    }

    # doing the insert here as well, since most of the test will require this paramset inserted
    ephys.ClusteringParamSet.insert_new_params(
        'kilosort2', 0, 'Spike sorting using Kilosort2', params_ks)

    yield params_ks

    ephys.ClusteringParamSet.delete()


@pytest.fixture
def ephys_recordings(pipeline, ingest_sessions):
    _, _, ephys, _, _, _ = pipeline

    ephys.EphysRecording.populate()

    yield

    ephys.EphysRecording.delete()


@pytest.fixture
def clustering_tasks(pipeline, kilosort_paramset, ephys_recordings):
    _, _, ephys, _, _, get_ephys_root_data_dir = pipeline

    root_dir = pathlib.Path(get_ephys_root_data_dir())
    for ephys_rec_key in (ephys.EphysRecording - ephys.ClusteringTask).fetch('KEY'):
        ephys_file = root_dir / (ephys.EphysRecording.EphysFile & ephys_rec_key).fetch('file_path')[0]
        recording_dir = ephys_file.parent
        kilosort_dir = next(recording_dir.rglob('spike_times.npy')).parent
        ephys.ClusteringTask.insert1({**ephys_rec_key,
                                      'paramset_idx': 0,
                                      'clustering_output_dir': kilosort_dir.as_posix()})

    ephys.Clustering.populate()

    yield

    ephys.ClusteringTask.delete()


@pytest.fixture
def clustering(clustering_tasks, pipeline):
    _, _, ephys, _, _, _ = pipeline

    ephys.Clustering.populate()

    yield

    ephys.Clustering.delete()


@pytest.fixture
def curations(clustering, pipeline):
    _, _, ephys, _, _, _ = pipeline

    for key in (ephys.ClusteringTask - ephys.Curation).fetch('KEY'):
        ephys.Curation().create1_from_clustering_task(key)

    yield

    ephys.Curation.delete()

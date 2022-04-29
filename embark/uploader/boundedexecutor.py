# pylint: disable=R1732, C0201, E1129
import csv
import logging
import os
import shutil
from subprocess import Popen, PIPE
import re
import json

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore

from django.utils.datetime_safe import datetime
from django.conf import settings

from uploader.archiver import Archiver
from uploader.models import FirmwareAnalysis, Result
from embark.logreader import LogReader


logger = logging.getLogger('web')

# maximum concurrent running workers
MAX_WORKERS = 4
# maximum queue bound
MAX_QUEUE = MAX_WORKERS

# assign the threadpool max_worker_threads
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
# create semaphore to track queue state
semaphore = BoundedSemaphore(MAX_QUEUE)

# emba directories
EMBA_SCRIPT_LOCATION = f"cd {settings.EMBA_ROOT} && sudo ./emba.sh"


class BoundedExecutor:
    """
    class BoundedExecutor
    This class is a wrapper of ExecuterThreadPool to enable a limited queue
    Used to handle concurrent emba analysis as well as emba.log analyzer
    """

    @classmethod
    def run_emba_cmd(cls, cmd, hashid=None, active_analyzer_dir=None):
        """
        run shell commands from python script as subprocess, waits for termination and evaluates returncode

        :param cmd: shell command to be executed
        :param hashid: primary key for firmware entry db identification
        :param active_analyzer_dir: active analyzer dir for deletion afterwards

        :return:
        """

        logger.info("Starting: %s", cmd)

        # get return code to evaluate: 0 = success, 1 = failure,
        # see emba.sh for further information
        try:

            analysis = FirmwareAnalysis.objects.get(fwA_id=hashid)

            # The os.setsid() is passed in the argument preexec_fn so it's run after the fork() and before  exec() to run the shell.
            proc = Popen(cmd, stdout=PIPE, shell=True, preexec_fn=os.setsid)  #nosec
            # Add proc to FirmwareAnalysis-Object
            analysis.pid=proc.pid

            # success
            logger.info("Success: %s", cmd)

            # get csv log location
            csv_log_location = f"{settings.EMBA_LOG_ROOT}/{hashid}/f50_base_aggregator.csv"

            # read f50_aggregator and store it into a Result form
            logger.info('Reading report from: %s', csv_log_location)
            # if Path(csv_log_location).exists:
            if Path(csv_log_location).is_file():
                cls.csv_read(hashid, csv_log_location, cmd)
            else:
                logger.error("CSV file %s for report: %s not generated", csv_log_location, hashid)
                logger.error("EMBA run was probably not successful!")

            # take care of cleanup
            if active_analyzer_dir:
                shutil.rmtree(active_analyzer_dir)

        except Exception as execpt:
            # fail
            logger.error("EMBA run was probably not successful!")
            logger.error("run_emba_cmd error: %s", execpt)

        finally:
            # finalize db entry
            if hashid:
                analysis.end_date = datetime.now()
                analysis.scan_time = datetime.now() - analysis.start_date
                analysis.duration = str(analysis.scan_time)
                analysis.finished = True
                analysis.save()

            logger.info("Successful cleaned up: %s", cmd)

    @classmethod
    def submit_firmware(cls, firmware_flags, firmware_file):
        """
        submit firmware + metadata for emba execution

        params firmware_flags: firmware model with flags and metadata
        params firmware_file: firmware file model to be analyzed

        return: emba process future on success, None on failure
        """
        active_analyzer_dir = f"{settings.ACTIVE_FW}{firmware_flags.id}/"
        Archiver.copy(firmware_file.file.path, active_analyzer_dir)

        # find emba start_file
        emba_startfile = os.listdir(active_analyzer_dir)
        if len(emba_startfile) == 1:
            image_file_location = f"{active_analyzer_dir}{emba_startfile.pop()}"
        else:
            logger.error("Uploaded file: %s doesnt comply with processable files.", firmware_file)
            logger.error("Zip folder with no extra directory in between.")
            shutil.rmtree(active_analyzer_dir)
            return None

        # get emba flags from command parser
        emba_flags = firmware_flags.get_flags()

        # evaluate meta information and safely create log dir

        emba_log_location = f"{settings.EMBA_LOG_ROOT}/{firmware_flags.id}"
        log_path = Path(emba_log_location)
        log_path.mkdir(parents=True, exist_ok=True)

        firmware_flags.path_to_logs = emba_log_location
        firmware_flags.save()

        # build command
        emba_cmd = f"{EMBA_SCRIPT_LOCATION} -f {image_file_location} -l {emba_log_location} {emba_flags}"

        # submit command to executor threadpool
        emba_fut = BoundedExecutor.submit(cls.run_emba_cmd, emba_cmd, firmware_flags.id, active_analyzer_dir)

        # start log_reader TODO: cancel future and return future
        # log_read_fut = BoundedExecutor.submit(LogReader, firmware_flags.pk)
        BoundedExecutor.submit(LogReader, firmware_flags.id)

        return emba_fut

    @classmethod
    def submit(cls, function_cmd, *args, **kwargs):
        """
        same as concurrent.futures.Executor#submit, but with queue

        params: see concurrent.futures.Executor#submit

        return: future on success, None on full queue
        """

        logger.info("submit fn: %s", function_cmd)
        logger.info("submit cls: %s", cls)

        # check if semaphore can be acquired, if not queue is full
        queue_not_full = semaphore.acquire(blocking=False)
        if not queue_not_full:
            logger.error("Executor queue full")
            return None
        try:
            future = executor.submit(function_cmd, *args, **kwargs)
        except Exception as error:
            logger.error("Executor task could not be submitted")
            semaphore.release()
            raise error
        else:
            future.add_done_callback(lambda x: semaphore.release())
            return future

    @classmethod
    def shutdown(cls, wait=True):
        """See concurrent.futures.Executor#shutdown"""

        executor.shutdown(wait)

    @classmethod
    def csv_read(cls, hashid, path, cmd):
        """
        This job reads the F50_aggregator file and stores its content into the Result model
        """

        with open(path, newline='\n', encoding='utf-8') as csv_file:
            csv_reader = csv.reader(csv_file, delimiter=';')
            csv_list = []
            for row in csv_reader:
                csv_list.append(row)
                res_dict = {}
                for ele in csv_list:
                    if len(ele) == 2:
                        res_dict[ele[0]] = ele[1]
                    elif len(ele) == 3:
                        if not ele[0] in res_dict.keys():
                            res_dict[ele[0]] = {}
                        res_dict[ele[0]][ele[1]] = ele[2]
                    else:
                        pass

        logger.info("result dict: %s", res_dict)
        res_dict.pop('FW_path', None)

        entropy_value = res_dict.get("entropy_value", 0)
        # if type(entropy_value) is str:
        if isinstance(entropy_value, str):
            # entropy_value = re.findall(r'(\d+\.?\d*)', ' 7.55 bits per byte.')[0]
            entropy_value = re.findall(r'(\d+\.?\d*)', entropy_value)[0]
            entropy_value = entropy_value.strip('.')

        res = Result(
            firmware=FirmwareAnalysis.objects.get(fwA_id=hashid),
            emba_command=cmd.replace(f"cd {settings.EMBA_ROOT} && ", ""),
            architecture_verified=res_dict.get("architecture_verified", ''),
            # os_unverified=res_dict.get("os_unverified", ''),
            os_verified=res_dict.get("os_verified", ''),
            files=int(res_dict.get("files", 0)),
            directories=int(res_dict.get("directories", 0)),
            entropy_value=float(entropy_value),
            shell_scripts=int(res_dict.get("shell_scripts", 0)),
            shell_script_vulns=int(res_dict.get("shell_script_vulns", 0)),
            kernel_modules=int(res_dict.get("kernel_modules", 0)),
            kernel_modules_lic=int(res_dict.get("kernel_modules_lic", 0)),
            interesting_files=int(res_dict.get("interesting_files", 0)),
            post_files=int(res_dict.get("post_files", 0)),
            canary=int(res_dict.get("canary", 0)),
            canary_per=int(res_dict.get("canary_per", 0)),
            relro=int(res_dict.get("relro", 0)),
            relro_per=int(res_dict.get("relro_per", 0)),
            no_exec=int(res_dict.get("no_exec", 0)),
            no_exec_per=int(res_dict.get("no_exec_per", 0)),
            pie=int(res_dict.get("pie", 0)),
            pie_per=int(res_dict.get("pie_per", 0)),
            stripped=int(res_dict.get("stripped", 0)),
            stripped_per=int(res_dict.get("stripped_per", 0)),
            bins_checked=int(res_dict.get("bins_checked", 0)),
            strcpy=int(res_dict.get("strcpy", 0)),
            strcpy_bin=json.dumps(res_dict.get("strcpy_bin", {})),
            versions_identified=int(res_dict.get("versions_identified", 0)),
            cve_high=int(res_dict.get("cve_high", 0)),
            cve_medium=int(res_dict.get("cve_medium", 0)),
            cve_low=int(res_dict.get("cve_low", 0)),
            exploits=int(res_dict.get("exploits", 0)),
            metasploit_modules=int(res_dict.get("metasploit_modules", 0)),
            certificates=int(res_dict.get("certificates", 0)),
            certificates_outdated=int(res_dict.get("certificates_outdated", 0)),
        )
        res.save()
        return res

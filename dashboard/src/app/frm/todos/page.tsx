import FrmTable from '@/components/FrmTable';
import { FRM } from '@/lib/frm';

/**
 * To-do — the one-off jobs, each one optionally about something that recurs.
 *
 * A subscription sits there being billed and a routine has to be attended,
 * but both throw off single jobs: approve the price rise on Netflix, fill in
 * the form for this year's disco. Those are not part of the recurring thing
 * and they are not free-floating either, so the register shows what each job
 * is about and, where it belongs to one occasion rather than the series,
 * which occasion.
 */
export default function Page() {
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">{FRM['todos'].label}</h2>
      <p className="max-w-3xl text-xs text-gray-500">
        <span className="text-gray-300">Due</span> is when the thing itself falls due.{' '}
        <span className="text-gray-300">Review</span> is when to look at this again — a job
        with no due date still has one, and every fresh email about the same job pushes it
        out, so a job only surfaces once whoever was asking has gone quiet.{' '}
        <span className="text-gray-300">About</span> links it to the subscription or routine
        it concerns; <span className="text-gray-300">Occasion</span> narrows that to a single
        occurrence, for a job that belongs to this year&apos;s disco rather than to the disco.
      </p>
      <FrmTable entity="todos" />
    </div>
  );
}

import FrmTable from '@/components/FrmTable';
import { FRM } from '@/lib/frm';

export default function Page() {
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">{FRM['family'].label}</h2>
      <FrmTable entity="family" />
    </div>
  );
}
